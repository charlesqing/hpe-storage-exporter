#!/usr/bin/python3
# -*- coding: utf-8 -*-

import time
import argparse
import logging
import paramiko
import pywbem
import sys
from prometheus_client import start_http_server
from prometheus_client.core import GaugeMetricFamily, REGISTRY


# 配置日志
logging.basicConfig(
    filename="/tmp/hp_3par_state.log",
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("hp_3par_logger")


class HP3PARCollector:
    def __init__(self, hp_user, hp_password, hp_ip, hp_port):
        self.hp_user = hp_user
        self.hp_password = hp_password
        self.hp_ip = hp_ip
        self.hp_port = hp_port
        self.cim_classes = [
            'TPD_DynamicStoragePool', 'TPD_NodeSystem', 'TPD_DriveCage', 'TPD_DiskDrive',
            'TPD_CagePowerSupply', 'TPD_NodePowerSupply', 'TPD_Battery', 'TPD_Fan',
            'TPD_IDEDrive', 'TPD_PhysicalMemory', 'TPD_SASPort', 'TPD_FCPort',
            'TPD_EthernetPort', 'TPD_PCICard'
        ]

    def collect(self):
        metrics = self._get_metrics()
        for metric in metrics.values():
            yield metric

    def _get_metrics(self):
        """获取所有指标"""
        metrics = {}
        hp_connect = self._wbem_connect()
        self._update_resource_metrics(hp_connect, metrics)
        self._update_overprovisioning_metrics(hp_connect, metrics)
        return metrics

    def _wbem_connect(self):
        """建立 WBEM 连接"""
        try:
            wbem_url = f"https://{self.hp_ip}:{self.hp_port}"
            return pywbem.WBEMConnection(
                wbem_url, (self.hp_user, self.hp_password),
                default_namespace="root/tpd", no_verification=True, timeout=50
            )
        except Exception as e:
            logger.error(f"WBEM Connection Error: {e}")
            sys.exit(1)

    def _ssh_connect(self):
        """建立 SSH 连接"""
        try:
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh_client.connect(hostname=self.hp_ip, username=self.hp_user, password=self.hp_password, port=22)
            return ssh_client
        except Exception as e:
            logger.error(f"SSH Connection Error: {e}")
            sys.exit(1)

    def _update_resource_metrics(self, hp_connect, metrics):
        """更新资源状态指标"""
        try:
            for cim_class in self.cim_classes:
                instances = hp_connect.EnumerateInstances(
                    cim_class, PropertyList=["OperationalStatus", "HealthState", "DeviceID", "ElementName", "Tag", "SerialNumber", "SystemLED", "OtherOperationalStatus", "RemainingCapacity", "Voltage"]
                )
                for instance in instances:
                    self._add_instance_metrics(cim_class, instance, metrics)
        except Exception as e:
            logger.error(f"Error updating resource metrics: {e}")
            sys.exit(1)

    def _add_instance_metrics(self, cim_class, instance, metrics):
        """为每个实例添加指标"""
        device_id = instance.get("DeviceID", "")
        element_name = instance.get("ElementName", "")
        tag = instance.get("Tag", "")
        serial_number = instance.get("SerialNumber", "")

        # 生成唯一标识符，并替换非法字符
        identifier = device_id or element_name or tag or serial_number
        identifier = identifier.replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_').replace(' ', '_')  # 替换空格为下划线

        # 添加健康状态指标
        if "HealthState" in instance and instance["HealthState"] is not None:
            key = f"health_{cim_class[4:]}_{identifier}"
            metrics[key] = GaugeMetricFamily(key, f'Health State of {cim_class}')
            metrics[key].add_metric([], float(instance["HealthState"]))

        # 添加操作状态指标
        if "OperationalStatus" in instance and instance["OperationalStatus"] and instance["OperationalStatus"][0] is not None:
            key = f"oper_{cim_class[4:]}_{identifier}"
            metrics[key] = GaugeMetricFamily(key, f'Operational State of {cim_class}')
            metrics[key].add_metric([], float(instance["OperationalStatus"][0]))

        # 处理电池容量和电压
        if cim_class == 'TPD_Battery':
            if "RemainingCapacity" in instance and instance["RemainingCapacity"] is not None:
                key = f"battery_capacity_{identifier}"
                metrics[key] = GaugeMetricFamily(key, 'Remaining Capacity of Battery')
                metrics[key].add_metric([], float(instance["RemainingCapacity"]))
            if "Voltage" in instance and instance["Voltage"] is not None:
                key = f"battery_voltage_{identifier}"
                metrics[key] = GaugeMetricFamily(key, 'Voltage of Battery')
                metrics[key].add_metric([], float(instance["Voltage"]))

        # 处理系统 LED 和其他操作状态
        if cim_class == 'TPD_NodeSystem' and "SystemLED" in instance and instance["SystemLED"] is not None:
            key = f"led_{cim_class[4:]}_{identifier}"
            metrics[key] = GaugeMetricFamily(key, f'LED State of {cim_class}')
            metrics[key].add_metric([], float(instance["SystemLED"]))

        if cim_class in ['TPD_SASPort', 'TPD_FCPort', 'TPD_EthernetPort'] and "OtherOperationalStatus" in instance and instance["OtherOperationalStatus"] is not None:
            key = f"other_oper_{cim_class[4:]}_{identifier}"
            metrics[key] = GaugeMetricFamily(key, f'Other Operational State of {cim_class}')
            metrics[key].add_metric([], float(instance["OtherOperationalStatus"]))

    def _update_overprovisioning_metrics(self, hp_connect, metrics):
        """更新超配指标"""
        ssh_client = self._ssh_connect()
        try:
            cpgs = hp_connect.EnumerateInstances('TPD_DynamicStoragePool', PropertyList=["ElementName"])
            for cpg in cpgs:
                cpg_name = cpg["ElementName"]
                stdin, stdout, stderr = ssh_client.exec_command(f'showspace -cpg {cpg_name}')
                output = stdout.read().decode("utf-8").split("\n")[3]
                overprv_value = float(output.split()[-1])
                key = f"overprv_DynamicStoragePool_{cpg_name.replace('.', '_').replace(' ', '_')}"  # 替换空格为下划线
                metrics[key] = GaugeMetricFamily(key, f'Overprovisioning of {cpg_name}')
                metrics[key].add_metric([], overprv_value)
        except Exception as e:
            logger.error(f"Error updating overprovisioning metrics: {e}")
        finally:
            ssh_client.close()


def main():
    parser = argparse.ArgumentParser(description="HP 3PAR Exporter")
    parser.add_argument('--hp_ip', required=True, help="HP 3PAR IP address")
    parser.add_argument('--hp_port', default=5989, help="HP 3PAR WBEM port")
    parser.add_argument('--hp_user', required=True, help="HP 3PAR username")
    parser.add_argument('--hp_password', required=True, help="HP 3PAR password")
    parser.add_argument('--port', default=9101, type=int, help="Exporter port")
    args = parser.parse_args()

    logger.info("Starting HP 3PAR Exporter")
    REGISTRY.register(HP3PARCollector(args.hp_user, args.hp_password, args.hp_ip, args.hp_port))
    start_http_server(args.port)
    logger.info(f"Exporter started on port {args.port}")

    while True:
        time.sleep(10)


if __name__ == "__main__":
    main()

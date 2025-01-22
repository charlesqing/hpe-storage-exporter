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
        
        # 初始化连接
        self.ssh_client = None  # type: paramiko.SSHClient
        self.wbem_conn = None  # type: pywbem.WBEMConnection
        self._initialize_connections()

    def _initialize_connections(self):
        """初始化并保持持久连接"""
        # 初始化WBEM连接
        try:
            wbem_url = f"https://{self.hp_ip}:{self.hp_port}"
            self.wbem_conn = pywbem.WBEMConnection(
                wbem_url, (self.hp_user, self.hp_password),
                default_namespace="root/tpd", no_verification=True, timeout=50
            )
        except Exception as e:
            logger.error(f"WBEM Connection Error: {e}")
            sys.exit(1)

        # 初始化SSH连接
        self._ssh_connect()

    def _ssh_connect(self):
        """建立/重建SSH连接"""
        try:
            if self.ssh_client is not None:
                try:
                    self.ssh_client.close()
                except Exception:
                    pass

            self.ssh_client = paramiko.SSHClient()
            # 使用更安全的策略
            self.ssh_client.set_missing_host_key_policy(paramiko.WarningPolicy())
            self.ssh_client.connect(
                hostname=self.hp_ip,
                username=self.hp_user,
                password=self.hp_password,
                port=22,
                timeout=10
            )
            logger.info("SSH connection established")
        except Exception as e:
            logger.error(f"SSH Connection Error: {e}")
            sys.exit(1)

    def __del__(self):
        """清理资源"""
        if self.ssh_client:
            try:
                self.ssh_client.close()
            except Exception:
                pass

    def collect(self):
        metrics = self._get_metrics()
        for metric in metrics.values():
            yield metric

    def _get_metrics(self):
        """获取所有指标"""
        metrics = {}
        try:
            self._validate_wbem_connection()
            self._update_resource_metrics(metrics)
            self._update_overprovisioning_metrics(metrics)
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")
        return metrics

    def _validate_wbem_connection(self):
        """验证WBEM连接有效性"""
        try:
            self.wbem_conn.EnumerateClassNames()
        except (pywbem.Error, TimeoutError):
            logger.warning("WBEM connection lost, reconnecting...")
            self._initialize_connections()

    def _update_resource_metrics(self, metrics):
        """更新资源状态指标"""
        try:
            for cim_class in self.cim_classes:
                instances = self.wbem_conn.EnumerateInstances(
                    cim_class, PropertyList=["OperationalStatus", "HealthState", "DeviceID", 
                                           "ElementName", "Tag", "SerialNumber", "SystemLED", 
                                           "OtherOperationalStatus", "RemainingCapacity", "Voltage"]
                )
                for instance in instances:
                    self._add_instance_metrics(cim_class, instance, metrics)
        except pywbem.Error as e:
            logger.error(f"WBEM Error: {e}")
            raise

    def _add_instance_metrics(self, cim_class, instance, metrics):
        """为每个实例添加指标（简化版）"""
        identifier = self._generate_identifier(instance)
        metric_prefix = f"hpe_{cim_class[4:]}"

        # Health State
        self._add_gauge_metric(
            metrics, f"{metric_prefix}_health", "Health State", 
            instance.get("HealthState"), identifier
        )

        # Operational Status
        op_status = instance.get("OperationalStatus")
        if op_status:
            self._add_gauge_metric(
                metrics, f"{metric_prefix}_oper", "Operational State",
                op_status[0], identifier
            )

        # 特殊指标处理
        if cim_class == 'TPD_Battery':
            self._add_gauge_metric(
                metrics, f"{metric_prefix}_capacity", "Battery Capacity",
                instance.get("RemainingCapacity"), identifier
            )
            self._add_gauge_metric(
                metrics, f"{metric_prefix}_voltage", "Battery Voltage",
                instance.get("Voltage"), identifier
            )

    def _generate_identifier(self, instance):
        """生成唯一标识符"""
        fields = ["DeviceID", "ElementName", "Tag", "SerialNumber"]
        parts = []
        for field in fields:
            value = instance.get(field)
            if value:
                parts.append(str(value).replace(' ', '_'))
        return "_".join(parts) or "unknown"

    def _add_gauge_metric(self, metrics, name, desc, value, identifier):
        """通用指标添加方法"""
        if value is None:
            return

        if name not in metrics:
            metrics[name] = GaugeMetricFamily(name, desc, labels=['tag'])
        
        # 使用集合跟踪已处理的标签
        if not hasattr(metrics[name], '_processed_tags'):
            metrics[name]._processed_tags = set()
        
        if identifier not in metrics[name]._processed_tags:
            metrics[name].add_metric([identifier], float(value))
            metrics[name]._processed_tags.add(identifier)

    def _update_overprovisioning_metrics(self, metrics):
        """更新超配指标（含SSH重试机制）"""
        max_retries = 2
        for attempt in range(max_retries):
            try:
                if not self.ssh_client.get_transport() or not self.ssh_client.get_transport().is_active():
                    logger.warning("SSH connection is closed, reconnecting...")
                    self._ssh_connect()

                cpgs = self.wbem_conn.EnumerateInstances('TPD_DynamicStoragePool', PropertyList=["ElementName"])
                for cpg in cpgs:
                    cpg_name = cpg["ElementName"]
                    stdin, stdout, stderr = self.ssh_client.exec_command(f'showspace -cpg {cpg_name}')
                    output = stdout.read().decode("utf-8")
                    if "invalid" in output.lower():
                        logger.error(f"Invalid command output: {output}")
                        continue

                    try:
                        overprv_value = float(output.split("\n")[3].split()[-1])
                    except (IndexError, ValueError) as e:
                        logger.error(f"Error parsing output: {e}")
                        continue

                    self._add_gauge_metric(
                        metrics, "hpe_overprv", "Overprovisioning", 
                        overprv_value, cpg_name.replace(' ', '_')
                    )
                break  # 成功则退出重试循环
            except (paramiko.SSHException, EOFError) as e:
                logger.warning(f"SSH error occurred ({attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    self._ssh_connect()
                else:
                    logger.error("Max SSH retries reached")
                    raise


def main():
    parser = argparse.ArgumentParser(description="HP 3PAR Exporter")
    parser.add_argument('--hp_ip', required=True, help="HP 3PAR IP address")
    parser.add_argument('--hp_port', default=5989, help="HP 3PAR WBEM port")
    parser.add_argument('--hp_user', required=True, help="HP 3PAR username")
    parser.add_argument('--hp_password', required=True, help="HP 3PAR password")
    parser.add_argument('--port', default=9101, type=int, help="Exporter port")
    args = parser.parse_args()

    logger.info("Starting HP 3PAR Exporter")
    collector = HP3PARCollector(args.hp_user, args.hp_password, args.hp_ip, args.hp_port)
    REGISTRY.register(collector)
    start_http_server(args.port)
    logger.info(f"Exporter started on port {args.port}")

    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Exporter stopped")


if __name__ == "__main__":
    main()

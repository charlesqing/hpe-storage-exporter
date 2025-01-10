#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import time
import argparse
import sys
import json
import logging
import logging.handlers
from re import findall
import pywbem
import paramiko
from prometheus_client import start_http_server, Gauge, Info, CollectorRegistry
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily, SummaryMetricFamily, REGISTRY


# 创建日志对象
LOG_FILENAME = "/tmp/hp_3par_state.log"
hp_logger = logging.getLogger("hp_3par_logger")
hp_logger.setLevel(logging.INFO)

# 设置日志处理器
hp_handler = logging.handlers.RotatingFileHandler(LOG_FILENAME, maxBytes=(1024**2)*10, backupCount=5)
hp_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# 设置日志格式
hp_handler.setFormatter(hp_formatter)

# 添加日志处理器
hp_logger.addHandler(hp_handler)

# Prometheus 指标
class HP3PARCollector(object):
    def __init__(self, hp_user, hp_password, hp_ip, hp_port, storage_name):
        self.hp_user = hp_user
        self.hp_password = hp_password
        self.hp_ip = hp_ip
        self.hp_port = hp_port
        self.storage_name = storage_name
        self.list_CIM_classes = [
            'TPD_DynamicStoragePool', 'TPD_NodeSystem', 'TPD_DriveCage', 'TPD_DiskDrive',
            'TPD_CagePowerSupply', 'TPD_NodePowerSupply', 'TPD_Battery', 'TPD_Fan',
            'TPD_IDEDrive', 'TPD_PhysicalMemory', 'TPD_SASPort', 'TPD_FCPort',
            'TPD_EthernetPort', 'TPD_PCICard'
        ]

        self.health_metrics = {}
        self.oper_metrics = {}
        self.other_oper_metrics = {}
        self.led_metrics = {}
        self.battery_metrics = {}
        self.overprv_metrics = {}

    def collect(self):
        self._update_metrics()

        for name, metric in self.health_metrics.items():
            yield metric
        for name, metric in self.oper_metrics.items():
            yield metric
        for name, metric in self.other_oper_metrics.items():
            yield metric
        for name, metric in self.led_metrics.items():
            yield metric
        for name, metric in self.battery_metrics.items():
            yield metric
        for name, metric in self.overprv_metrics.items():
            yield metric

    def hp_wbem_connect(self):
        try:
            wbem_url = "https://{0}:{1}".format(self.hp_ip, self.hp_port)
            wbem_connect = pywbem.WBEMConnection(
                wbem_url, (self.hp_user, self.hp_password),
                default_namespace="root/tpd", no_verification=True, timeout=50
            )
            hp_logger.info("WBEM Connection Established Successfully")
            return wbem_connect
        except Exception as oops:
            hp_logger.error("WBEM Connection Error Occurs: {}".format(oops))
            sys.exit("1000")

    def hp_ssh_connect(self):
        try:
            hp_ssh_client = paramiko.SSHClient()
            hp_ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            hp_ssh_client.connect(hostname=self.hp_ip, username=self.hp_user, password=self.hp_password, port=22)
            hp_logger.info("SSH Connection Established Successfully")
            return hp_ssh_client
        except Exception as oops:
            hp_logger.error("SSH Connection Error Occurs: {}".format(oops))
            sys.exit("1000")

    def hp_ssh_logout(self, hp_ssh_client):
        try:
            hp_ssh_client.close()
            hp_logger.info("SSH Connection Closed Successfully")
        except Exception as oops:
            hp_logger.error("SSH Connection Close Error Occurs: {}".format(oops))

    def _update_metrics(self):
        """
        Collect HP 3PAR metrics
        """
        hp_connect = self.hp_wbem_connect()
        self._get_status_resources(hp_connect)
        self._get_overprovisioning(hp_connect)

    def _get_status_resources(self, hp_connect):
        """
        Get Status for resources
        """
        try:
            for CIM_class in self.list_CIM_classes:
                state_of_concrete_instances = hp_connect.EnumerateInstances(
                    CIM_class, PropertyList=["OperationalStatus", "HealthState", "DeviceID", "ElementName", "Tag", "SerialNumber", "SystemLED", "OtherOperationalStatus", "RemainingCapacity", "Voltage"]
                )
                for instance in state_of_concrete_instances:
                    if CIM_class in ['TPD_Fan', 'TPD_Battery', 'TPD_CagePowerSupply', 'TPD_NodePowerSupply']:
                        key_health_status = "health_{0}_{1}".format(CIM_class[4:], instance["DeviceID"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')
                        key_oper_status = "oper_{0}_{1}".format(CIM_class[4:], instance["DeviceID"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')

                        self.health_metrics[key_health_status] = GaugeMetricFamily(key_health_status, 'Health State of {0}'.format(CIM_class), labels=['storage_name'])
                        self.health_metrics[key_health_status].add_metric([self.storage_name], instance["HealthState"])

                        self.oper_metrics[key_oper_status] = GaugeMetricFamily(key_oper_status, 'Operational State of {0}'.format(CIM_class), labels=['storage_name'])
                        self.oper_metrics[key_oper_status].add_metric([self.storage_name], instance["OperationalStatus"][0])

                        # 处理 Battery 的 RemainingCapacity 和 Voltage
                        if CIM_class == 'TPD_Battery':
                            key_battery_capacity = "battery_capacity_{0}".format(instance["DeviceID"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')
                            key_battery_voltage = "battery_voltage_{0}".format(instance["DeviceID"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')
                            if instance["RemainingCapacity"] is not None:
                                self.battery_metrics[key_battery_capacity] = GaugeMetricFamily(key_battery_capacity, 'Remaining Capacity of Battery', labels=['storage_name'])
                                self.battery_metrics[key_battery_capacity].add_metric([self.storage_name], instance["RemainingCapacity"])
                            if instance["Voltage"] is not None:
                                self.battery_metrics[key_battery_voltage] = GaugeMetricFamily(key_battery_voltage, 'Voltage of Battery', labels=['storage_name'])
                                self.battery_metrics[key_battery_voltage].add_metric([self.storage_name], instance["Voltage"])

                    if CIM_class in ['TPD_NodeSystem', 'TPD_DriveCage', 'TPD_DiskDrive', 'TPD_DynamicStoragePool', 'TPD_SASPort', 'TPD_FCPort', 'TPD_EthernetPort']:
                        key_health_status = "health_{0}_{1}".format(CIM_class[4:], instance["ElementName"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')
                        key_oper_status = "oper_{0}_{1}".format(CIM_class[4:], instance["ElementName"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')

                        self.health_metrics[key_health_status] = GaugeMetricFamily(key_health_status, 'Health State of {0}'.format(CIM_class), labels=['storage_name'])
                        self.health_metrics[key_health_status].add_metric([self.storage_name], instance["HealthState"])

                        self.oper_metrics[key_oper_status] = GaugeMetricFamily(key_oper_status, 'Operational State of {0}'.format(CIM_class), labels=['storage_name'])
                        self.oper_metrics[key_oper_status].add_metric([self.storage_name], instance["OperationalStatus"][0])

                        if CIM_class == 'TPD_NodeSystem':
                            key_system_led = "led_{0}_{1}".format(CIM_class[4:], instance["ElementName"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')
                            self.led_metrics[key_system_led] = GaugeMetricFamily(key_system_led, 'LED State of {0}'.format(CIM_class), labels=['storage_name'])
                            self.led_metrics[key_system_led].add_metric([self.storage_name], instance["SystemLED"])
                        elif CIM_class in ['TPD_SASPort', 'TPD_FCPort', 'TPD_EthernetPort']:
                            key_other_oper_status = "other_oper_{0}_{1}".format(CIM_class[4:], instance["ElementName"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')
                            self.other_oper_metrics[key_other_oper_status] = GaugeMetricFamily(key_other_oper_status, 'Other Operational State of {0}'.format(CIM_class), labels=['storage_name'])
                            self.other_oper_metrics[key_other_oper_status].add_metric([self.storage_name], instance["OtherOperationalStatus"])

                    if CIM_class in ['TPD_IDEDrive', 'TPD_PCICard']:
                        key_oper_status = "oper_{0}_{1}".format(CIM_class[4:], instance["Tag"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')
                        self.oper_metrics[key_oper_status] = GaugeMetricFamily(key_oper_status, 'Operational State of {0}'.format(CIM_class), labels=['storage_name'])
                        self.oper_metrics[key_oper_status].add_metric([self.storage_name], instance["OperationalStatus"][0])

                    if CIM_class == 'TPD_PhysicalMemory':
                        key_oper_status = "oper_{0}_{1}".format(CIM_class[4:], instance["SerialNumber"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')
                        self.oper_metrics[key_oper_status] = GaugeMetricFamily(key_oper_status, 'Operational State of {0}'.format(CIM_class), labels=['storage_name'])
                        self.oper_metrics[key_oper_status].add_metric([self.storage_name], instance["OperationalStatus"][0])
        except Exception as oops:
            hp_logger.error("Error occurs in getting status - {0}".format(oops))
            sys.exit("1100")

    def _get_overprovisioning(self, hp_connect):
        """
        Get Overprovisioning of CPGs
        """
        hp_ssh_connection = self.hp_ssh_connect()

        try:
            CPGs = hp_connect.EnumerateInstances('TPD_DynamicStoragePool', PropertyList=["ElementName"])

            for cpg in CPGs:
                stdin, stdout, stderr = hp_ssh_connection.exec_command('showspace -cpg {0}'.format(cpg["ElementName"]))
                overprv_raw_value = stdout.read()
                overprv_raw_value = overprv_raw_value.decode("utf-8")
                overprv_raw_value = overprv_raw_value.split("\n")
                overprv_raw_value = overprv_raw_value[3]

                overprv_raw_value = overprv_raw_value.split(' ')
                value_overprv = float(overprv_raw_value[-1:][0])
                key_overprv = "overprv_DynamicStoragePool_{0}".format(cpg["ElementName"]).replace('.', '_').replace('[', '_').replace(']', '_').replace('-', '_')

                self.overprv_metrics[key_overprv] = GaugeMetricFamily(key_overprv, 'Overprovisioning of {0}'.format(cpg["ElementName"]), labels=['storage_name'])
                self.overprv_metrics[key_overprv].add_metric([self.storage_name], value_overprv)
        except Exception as oops:
            hp_logger.error("An error occurs in overprovision - {0}".format(oops))
            sys.exit("1000")

        self.hp_ssh_logout(hp_ssh_connection)


def main():
    hp_parser = argparse.ArgumentParser()
    hp_parser.add_argument('--hp_ip', action="store", required=True)
    hp_parser.add_argument('--hp_port', action="store", required=False, default=5989)
    hp_parser.add_argument('--hp_user', action="store", required=True)
    hp_parser.add_argument('--hp_password', action="store", required=True)
    hp_parser.add_argument('--storage_name', action="store", required=True)
    hp_parser.add_argument('--port', action="store", required=False, default=9101, type=int, help="Port to expose metrics on")
    arguments = hp_parser.parse_args()

    # 使用 argparse 获取 storage_name
    STORAGE_NAME = arguments.storage_name
    hp_logger.info(f"Storage Name: {STORAGE_NAME}")  # 记录存储名称

    # Start up the server to expose the metrics.
    REGISTRY.register(HP3PARCollector(arguments.hp_user, arguments.hp_password, arguments.hp_ip, arguments.hp_port, STORAGE_NAME))
    start_http_server(arguments.port)
    hp_logger.info("Exporter is started on port {0}".format(arguments.port))
    while True:
        time.sleep(10)


if __name__ == "__main__":
    main()

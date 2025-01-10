# HPE Storage Exporter

### Prerequisites

Before you begin, ensure you have the following dependencies installed on your system:

- `libffi-devel`
- `python3`
- `python3-devel`
- `rust`

You can install these dependencies using the following command:

`sudo yum install -y libffi-devel python3 python3-devel rust`

### Upgrade pip

Ensure you have the latest version of pip installed. This can be done using the following command:

`sudo pip3 install --upgrade pip`

### Install Required Packages

Install the necessary Python packages using pip:

`sudo pip3 install setuptools_rust wheel`

### Run Your Script

To run the HPE Storage Exporter script, use the following command:

`python3 hpe-3par-exporter.py --hp_ip 10.10.20.20 --hp_port 5989 --hp_user USERNAME --hp_password PASSWORD --storage_name HPE-Alletra-MP --port 9101`

### Start Your Web

`http://IP:9101/metrics`

### Command Line Arguments

The script accepts the following command line arguments:

- `--hp_ip`: The IP address of the HPE storage system.
- `--hp_port`: The port number to use for the connection (default: 5989).
- `--hp_user`: The username for authentication.
- `--hp_password`: The password for authentication.
- `--storage_name`: The name of the storage system.
- `--port`: The port number to use for the exporter (default: 9101).

## Troubleshooting

If you encounter any issues, please ensure that:

1. All dependencies are correctly installed.
2. The pip version is up to date.
3. The Rust compiler is installed and available in the PATH.
4. The script has the necessary permissions to access the HPE storage system.

## Contributing

Contributions are welcome! Please feel free to open issues or submit pull requests.

## License

This project is licensed under the [MIT License](LICENSE).

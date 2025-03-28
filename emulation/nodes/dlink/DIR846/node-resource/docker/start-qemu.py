#!/usr/bin/python3
import os
import sys
import subprocess
import random

def gen_mac(last_octet=None):
    """ Generate a random MAC address that is in the qemu OUI space and that
        has the given last octet.
    """
    return "52:54:00:%02x:%02x:%02x" % (
        random.randint(0x00, 0xff),
        random.randint(0x00, 0xff),
        last_octet if last_octet is not None else random.randint(0x00, 0xff)
    )

def setup_port_forwarding(port_fwds):
    """ Set up port forwarding using socat before running QEMU. """
    for fwd in port_fwds:
        host_port, guest_port = fwd.split(":")
        host_port = int(host_port)
        guest_port = int(guest_port)

        # Run socat command to forward the host port to guest port
        socat_cmd = [
            "socat",
            f"TCP-LISTEN:{guest_port},fork",
            f"TCP:127.0.0.1:{host_port}"
        ]
        print(f"Setting up port forwarding: {socat_cmd}")
        subprocess.Popen(socat_cmd)  # Run socat in the background

# Assign variables
architecture = "mipsel"  # Example value, modify as needed
memsize = "512"         # Example value, adjust as needed
board = "malta"             # Example value, adjust as needed
kernel = "kernel"        # Example file name, change as needed
image = "image.qcow2"        # Example file name, change as needed
port_fwds = ["8080:80", "2222:22"] # Example array of port forwarding rules
console = "ttyS0"         # Example value, adjust as needed
root_dev = "/dev/sda1"    # Example value, adjust as needed
log_file_path = "/xwangnet/execv.log" # Define the log file path
mac = gen_mac()
initrd = ""      # Example file name, change as needed or set to None

# Networking flag
use_simple_net = True  # Set this flag as needed to toggle networking

# Define paths
kernel_path = f"/xwangnet/{kernel}"
image_path = f"/xwangnet/{image}"

# Check if kernel and image files exist
if not os.path.isfile(kernel_path):
    print(f"Error: Kernel file '{kernel}' does not exist in /node.")
    sys.exit(1)

if not os.path.isfile(image_path):
    print(f"Error: Image file '{image}' does not exist in /node.")
    sys.exit(1)

# Set up port forwarding using socat
setup_port_forwarding(port_fwds)

# Construct the networking arguments based on the flag
if use_simple_net:
    network_args = [
        "-net", "nic",
        "-net", "user"
    ]
    for fwd in port_fwds:
        host_port, guest_port = fwd.split(":")
        network_args[-1] += f",hostfwd=tcp::{host_port}-:{guest_port}"
else:
    network_args = [
        "-device", f"virtio-net-pci,netdev=p00,mac={mac}",
        "-netdev", f"user,id=p00"
    ]
    for fwd in port_fwds:
        host_port, guest_port = fwd.split(":")
        network_args[-1] += f",hostfwd=tcp::{host_port}-:{guest_port}"



# Run the QEMU command
qemu_command = [
    f"qemu-system-{architecture}",
    "-m", memsize, 
    "-M", board,
    "-kernel", kernel_path,
    "-hda", image_path,
    *network_args,
    "-serial", "mon:stdio",
    "-chardev", f"file,id=logfile,path={log_file_path}",
    "-serial", "chardev:logfile",
    "-append", f"root={root_dev} console={console}",
    "-nographic"
]

# Add initrd argument if initrd has a value
if initrd:
    initrd_path = f"/xwangnet/{initrd}"
    qemu_command.extend(["-initrd", initrd_path])

print(qemu_command)

try:
    subprocess.run(qemu_command, check=True)
except subprocess.CalledProcessError as e:
    print(f"Error running QEMU: {e}")
    sys.exit(1)

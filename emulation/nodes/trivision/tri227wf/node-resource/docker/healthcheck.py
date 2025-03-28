#!/usr/bin/env python3

import subprocess
import requests
import socket
import sys
import time

LOG_FILE = "/xwangnet/healthcheck.log"

# Define services and their respective ports
SERVICES = {
    "HTTP": [80],  # Example: Check multiple HTTP ports
    "SSH": [22],  # Example: Check only port 22 for SSH
    "CUSTOM": []  # Example: Redis, other TCP services
}

def log_status(protocol, command, output, status):
    """
    Logs the status of each check in the format:
    timestamp, protocol, check-command, output, status
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp}, {protocol}, {command}, {output}, {status}\n"
    
    with open(LOG_FILE, "a") as log_file:
        log_file.write(log_entry)

def run_check(protocol, command, check_function, *args):
    """
    Generic function to run a health check and log the result.
    """
    try:
        result = check_function(*args)
        status = "success" if result else "failure"
        log_status(protocol, command, str(result), status)
        return result
    except Exception as e:
        log_status(protocol, command, str(e), "error")
        return False

def get_docker_ip():
    """
    Retrieves the container's IP address.
    If it fails, returns "127.0.0.1" and logs a warning.
    """
    command = "hostname -I"
    try:
        result = subprocess.run(command.split(), capture_output=True, text=True)
        ip_address = result.stdout.strip().split()[0]  # Get the first IP
        if ip_address:
            log_status("system", command, ip_address, "success")
            return ip_address
    except Exception as e:
        log_status("system", command, str(e), "failure")

    fallback_ip = "127.0.0.1"
    log_status("system", command, f"Using fallback IP {fallback_ip}", "warning")
    return fallback_ip

def check_http(ip, port):
    """
    Sends a GET request to an HTTP service.
    Returns True if the response is healthy (2xx or 3xx), otherwise False.
    """
    url = f"http://{ip}:{port}"
    try:
        response = requests.get(url, timeout=5, allow_redirects=True)
        status_code = response.status_code

        if 200 <= status_code < 400:  # Accepts 2xx (OK) and 3xx (Redirects)
            return True
        elif 400 <= status_code < 500:  # 4xx errors (e.g., Unauthorized)
            log_status("HTTP", f"HTTP check {url}", f"Warning: {status_code}", "warning")
            return True  # Still considered healthy (service is running)
        else:
            return False  # Fail if 500+
    except requests.RequestException as e:
        log_status("HTTP", f"HTTP check {url}", str(e), "failure")
        return False


def check_ssh(ip, port):
    """
    Attempts to connect to an SSH service.
    Returns True if successful, otherwise False.
    """
    try:
        with socket.create_connection((ip, port), timeout=5) as sock:
            return True
    except (socket.timeout, ConnectionRefusedError):
        return False

def check_custom_service(ip, port):
    """
    Generic function for checking other services.
    Modify this function to add specific checks for new protocols.
    """
    try:
        with socket.create_connection((ip, port), timeout=5) as sock:
            return True
    except (socket.timeout, ConnectionRefusedError):
        return False

# Mapping protocols to check functions
CHECK_FUNCTIONS = {
    "HTTP": check_http,
    "SSH": check_ssh,
    "CUSTOM": check_custom_service  # Extend this mapping for other services
}

if __name__ == "__main__":
    # Clear previous logs
    with open(LOG_FILE, "w") as log_file:
        log_file.write("timestamp, protocol, check-command, output, status\n")

    docker_ip = get_docker_ip()
    overall_status = True  # Track overall health

    # Iterate through each protocol and their respective ports
    for protocol, ports in SERVICES.items():
        check_function = CHECK_FUNCTIONS.get(protocol)
        if check_function:
            for port in ports:
                command_desc = f"{protocol} check on port {port}"
                result = run_check(protocol, command_desc, check_function, docker_ip, port)
                overall_status &= result  # Update health status
        else:
            log_status(protocol, f"Unknown protocol {protocol}", "N/A", "error")
            overall_status = False  # Mark as unhealthy

    sys.exit(0 if overall_status else 1)

import subprocess
import re
import time
import logging

logger = logging.getLogger(__name__)

class InterfaceManager:
    """Manages macvlan interfaces and DHCP configuration for external IP binding"""
    
    PARENT_INTERFACE = 'ens18'
    MACVLAN_PREFIX = 'macvlan'
    DHCP_TIMEOUT = 30
    DHCP_PREFERRED_RANGE = (220, 254)
    
    @staticmethod
    def list_available_interfaces():
        """List all network interfaces with their IP addresses"""
        try:
            result = subprocess.run(
                ['ip', '-j', 'addr', 'show'],
                capture_output=True,
                text=True,
                check=True
            )
            
            import json
            interfaces = json.loads(result.stdout)
            
            available = []
            for iface in interfaces:
                ifname = iface.get('ifname')
                # Skip loopback and docker interfaces
                if ifname and not ifname.startswith(('lo', 'docker', 'br-', 'veth')):
                    addr_info = iface.get('addr_info', [])
                    ips = [addr['local'] for addr in addr_info if addr.get('family') == 'inet']
                    if ips:
                        available.append({'interface': ifname, 'ip': ips[0]})
            
            return available
        except Exception as e:
            logger.error(f"Failed to list interfaces: {e}")
            return []
    
    @staticmethod
    def get_available_interface_name():
        """Find next available macvlan interface name"""
        try:
            result = subprocess.run(
                ['ip', 'link', 'show'],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Find all existing macvlan interfaces
            existing = re.findall(r'macvlan(\d+):', result.stdout)
            existing_nums = [int(n) for n in existing]
            
            # Find first available number
            for i in range(100):
                if i not in existing_nums:
                    return f'{InterfaceManager.MACVLAN_PREFIX}{i}'
            
            raise Exception("No available macvlan interface names")
        except Exception as e:
            logger.error(f"Failed to find available interface name: {e}")
            raise
    
    @staticmethod
    def create_macvlan_interface(parent=None, name=None):
        """Create a new macvlan interface
        
        Args:
            parent: Parent interface (default: ens18)
            name: Interface name (auto-generated if not provided)
            
        Returns:
            str: Interface name
        """
        parent = parent or InterfaceManager.PARENT_INTERFACE
        name = name or InterfaceManager.get_available_interface_name()
        
        # Validate interface name: Linux interface names are up to 15 chars, alphanumeric, underscore, dash
        if not re.match(r'^[a-zA-Z0-9_-]{1,15}$', name):
            raise ValueError(f"Invalid interface name: {name!r}. Must be 1-15 chars, alphanumeric, underscore, or dash.")
        
        try:
            logger.info(f"Creating macvlan interface {name} on parent {parent}")
            
            # Create macvlan interface
            subprocess.run(
                ['ip', 'link', 'add', 'link', parent, 'name', name, 'type', 'macvlan', 'mode', 'bridge'],
                check=True,
                capture_output=True
            )
            
            # Bring interface up
            subprocess.run(
                ['ip', 'link', 'set', name, 'up'],
                check=True,
                capture_output=True
            )
            
            logger.info(f"Successfully created interface {name}")
            return name
            
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else str(e)
            logger.error(f"Failed to create interface {name}: {stderr_msg}")
            raise Exception(f"Failed to create macvlan interface: {stderr_msg}")
    
    @staticmethod
    def request_dhcp_ip(interface, preferred_range=None):
        """Request IP address via DHCP
        
        Args:
            interface: Interface name
            preferred_range: Tuple of (min, max) preferred IP range
            
        Returns:
            str: Assigned IP address or None
        """
        preferred_range = preferred_range or InterfaceManager.DHCP_PREFERRED_RANGE
        
        try:
            logger.info(f"Requesting DHCP IP for {interface}")
            
            # Run dhclient
            result = subprocess.run(
                ['dhclient', '-v', interface],
                capture_output=True,
                text=True,
                timeout=InterfaceManager.DHCP_TIMEOUT
            )
            
            # Wait a moment for IP assignment
            time.sleep(2)
            
            # Get assigned IP
            assigned_ip = InterfaceManager.get_interface_ip(interface)
            
            if assigned_ip:
                logger.info(f"Successfully assigned IP {assigned_ip} to {interface}")
                
                # Send gratuitous ARP
                try:
                    subprocess.run(
                        ['arping', '-c', '3', '-A', '-I', interface, assigned_ip],
                        capture_output=True,
                        timeout=5
                    )
                except Exception as e:
                    logger.warning(f"arping failed for {interface} ({assigned_ip}): {e}")
                
                return assigned_ip
            else:
                logger.error(f"DHCP succeeded but no IP assigned to {interface}")
                return None
                
        except subprocess.TimeoutExpired:
            logger.error(f"DHCP timeout for {interface}")
            return None
        except Exception as e:
            logger.error(f"DHCP request failed for {interface}: {e}")
            return None
    
    @staticmethod
    def assign_static_ip(interface, ip_address, netmask='24'):
        """Assign static IP to interface
        
        Args:
            interface: Interface name
            ip_address: IP address to assign
            netmask: Network mask (CIDR notation)
            
        Returns:
            bool: Success status
        """
        try:
            logger.info(f"Assigning static IP {ip_address}/{netmask} to {interface}")
            
            subprocess.run(
                ['ip', 'addr', 'add', f'{ip_address}/{netmask}', 'dev', interface],
                check=True,
                capture_output=True
            )
            
            logger.info(f"Successfully assigned static IP to {interface}")
            return True
            
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else str(e)
            logger.error(f"Failed to assign static IP: {stderr_msg}")
            return False
    
    @staticmethod
    def get_interface_ip(interface):
        """Get IP address of interface
        
        Args:
            interface: Interface name
            
        Returns:
            str: IP address or None
        """
        try:
            result = subprocess.run(
                ['ip', '-j', 'addr', 'show', interface],
                capture_output=True,
                text=True,
                check=True
            )
            
            import json
            data = json.loads(result.stdout)
            
            if data:
                addr_info = data[0].get('addr_info', [])
                for addr in addr_info:
                    if addr.get('family') == 'inet':
                        return addr.get('local')
            
            return None
        except Exception as e:
            logger.error(f"Failed to get IP for {interface}: {e}")
            return None
    
    @staticmethod
    def release_dhcp_ip(interface):
        """Release DHCP lease for interface
        
        Args:
            interface: Interface name
            
        Returns:
            bool: Success status
        """
        try:
            logger.info(f"Releasing DHCP lease for {interface}")
            
            subprocess.run(
                ['dhclient', '-r', interface],
                capture_output=True,
                timeout=10
            )
            
            logger.info(f"Successfully released DHCP lease for {interface}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to release DHCP lease: {e}")
            return False
    
    @staticmethod
    def delete_interface(interface):
        """Delete interface
        
        Args:
            interface: Interface name
            
        Returns:
            bool: Success status
        """
        try:
            logger.info(f"Deleting interface {interface}")
            
            # Bring interface down first
            subprocess.run(
                ['ip', 'link', 'set', interface, 'down'],
                capture_output=True
            )
            
            # Delete interface
            subprocess.run(
                ['ip', 'link', 'delete', interface],
                check=True,
                capture_output=True
            )
            
            logger.info(f"Successfully deleted interface {interface}")
            return True
            
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else str(e)
            logger.error(f"Failed to delete interface {interface}: {stderr_msg}")
            return False
    
    @staticmethod
    def test_interface_creation(parent=None):
        """Test if interface creation works
        
        Args:
            parent: Parent interface to test
            
        Returns:
            dict: Test results with success status and message
        """
        parent = parent or InterfaceManager.PARENT_INTERFACE
        test_interface = 'macvlan-test'
        
        try:
            # Check parent interface exists
            result = subprocess.run(
                ['ip', 'link', 'show', parent],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                return {
                    'success': False,
                    'message': f'Parent interface {parent} not found'
                }
            
            # Try to create test interface
            subprocess.run(
                ['ip', 'link', 'add', 'link', parent, 'name', test_interface, 'type', 'macvlan', 'mode', 'bridge'],
                check=True,
                capture_output=True
            )
            
            # Clean up test interface
            subprocess.run(
                ['ip', 'link', 'delete', test_interface],
                capture_output=True
            )
            
            return {
                'success': True,
                'message': 'Interface creation test passed'
            }
            
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else str(e)
            return {
                'success': False,
                'message': f'Interface creation test failed: {stderr_msg}'
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Unexpected error: {str(e)}'
            }
    
    @staticmethod
    def verify_interface_ip(interface):
        """Verify interface has IP address assigned
        
        Args:
            interface: Interface name
            
        Returns:
            dict: Verification result with success status, IP, and message
        """
        try:
            ip = InterfaceManager.get_interface_ip(interface)
            
            if ip:
                return {
                    'success': True,
                    'ip': ip,
                    'message': f'Interface {interface} has IP {ip}'
                }
            else:
                return {
                    'success': False,
                    'ip': None,
                    'message': f'Interface {interface} has no IP assigned'
                }
                
        except Exception as e:
            return {
                'success': False,
                'ip': None,
                'message': f'Error verifying interface: {str(e)}'
            }



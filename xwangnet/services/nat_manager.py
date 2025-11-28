import subprocess
import re
import logging
import ipaddress

logger = logging.getLogger(__name__)

class NATManager:
    """Manages iptables NAT rules for external IP binding"""
    
    @staticmethod
    def configure_full_port_nat(src_ip, dst_ip, macvlan_interface, bridge_interface, parent_interface='ens18'):
        """Configure NAT for all ports from external IP to container IP
        
        This implements the complete working NAT configuration discovered through testing:
        1. DNAT: Incoming traffic routing
        2. SNAT: Outgoing traffic source rewrite
        3. DOCKER-USER: Accept incoming to container  
        4. DOCKER-USER: Force outbound via macvlan0
        5. FORWARD: Allow replies to exit bridge (THE CRITICAL FIX)
        
        Args:
            src_ip: External macvlan IP address  
            dst_ip: Internal Docker container IP
            macvlan_interface: Macvlan interface name (e.g., macvlan0)
            bridge_interface: Docker bridge interface (e.g., br-431706091c0d)
            parent_interface: Parent interface (default: ens18)
            
        Returns:
            dict: Result with success status and list of applied rules
        """
        # Validate IP addresses
        try:
            ipaddress.ip_address(src_ip)
            ipaddress.ip_address(dst_ip)
        except ValueError as e:
            logger.error(f"Invalid IP address: {e}")
            return {'success': False, 'rules': [], 'message': f'Invalid IP address: {e}'}
        
        # Validate interface names
        if not re.match(r'^[a-zA-Z0-9_-]{1,15}$', macvlan_interface):
            logger.error(f"Invalid macvlan interface name: {macvlan_interface}")
            return {'success': False, 'rules': [], 'message': f'Invalid macvlan interface name'}
        if not re.match(r'^[a-zA-Z0-9_-]{1,15}$', bridge_interface):
            logger.error(f"Invalid bridge interface name: {bridge_interface}")
            return {'success': False, 'rules': [], 'message': f'Invalid bridge interface name'}
        
        applied_rules = []
        
        try:
            logger.info(f"Configuring full port NAT: {src_ip} ({macvlan_interface}) → {dst_ip}")
            
            # Rule 1: DNAT - Incoming: Route external traffic to container
            subprocess.run(
                ['iptables', '-t', 'nat', '-A', 'PREROUTING', '-d', src_ip, '-j', 'DNAT', '--to-destination', dst_ip],
                check=True,
                capture_output=True
            )
            applied_rules.append(f"nat PREROUTING -d {src_ip} -j DNAT --to-destination {dst_ip}")
            logger.info(f"✓ Applied DNAT rule")
            
            # Rule 2: SNAT - Outgoing: Make replies appear from macvlan IP
            subprocess.run(
                ['iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', dst_ip, '-j', 'SNAT', '--to-source', src_ip],
                check=True,
                capture_output=True
            )
            applied_rules.append(f"nat POSTROUTING -s {dst_ip} -j SNAT --to-source {src_ip}")
            logger.info(f"✓ Applied SNAT rule")
            
            # Rule 3: DOCKER-USER - Accept incoming to container
            subprocess.run(
                ['iptables', '-I', 'DOCKER-USER', '-d', dst_ip, '-j', 'ACCEPT'],
                check=True,
                capture_output=True
            )
            applied_rules.append(f"filter DOCKER-USER -d {dst_ip} -j ACCEPT")
            logger.info(f"✓ Applied DOCKER-USER incoming rule")
            
            # Rule 4: DOCKER-USER - Force outbound via macvlan0 only
            subprocess.run(
                ['iptables', '-I', 'DOCKER-USER', '-o', macvlan_interface, '-s', dst_ip, '-j', 'ACCEPT'],
                check=True,
                capture_output=True
            )
            applied_rules.append(f"filter DOCKER-USER -o {macvlan_interface} -s {dst_ip} -j ACCEPT")
            logger.info(f"✓ Applied DOCKER-USER outgoing (macvlan) rule")
            
            # Rule 5: FORWARD - Allow outbound from bridge (THE CRITICAL FIX)
            # This allows reply packets to exit the Docker bridge
            subprocess.run(
                ['iptables', '-I', 'FORWARD', '-i', bridge_interface, '-s', dst_ip, '-j', 'ACCEPT'],
                check=True,
                capture_output=True
            )
            applied_rules.append(f"filter FORWARD -i {bridge_interface} -s {dst_ip} -j ACCEPT")
            logger.info(f"✓ Applied FORWARD rule (critical fix for reply packets)")
            
            logger.info(f"Successfully configured all 5 NAT rules for {src_ip} → {dst_ip}")
            
            return {
                'success': True,
                'rules': applied_rules,
                'message': f'NAT configured: {src_ip} → {dst_ip}'
            }
            
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode() if e.stderr else str(e)
            logger.error(f"Failed to configure NAT: {stderr_msg}")
            # Attempt to roll back any applied rules
            NATManager._rollback_rules(applied_rules, macvlan_interface, bridge_interface)
            return {
                'success': False,
                'rules': [],
                'message': f'Failed to configure NAT: {stderr_msg}'
            }
        except Exception as e:
            logger.error(f"Unexpected error configuring NAT: {e}")
            NATManager._rollback_rules(applied_rules, macvlan_interface, bridge_interface)
            return {
                'success': False,
                'rules': [],
                'message': f'Unexpected error: {str(e)}'
            }
    
    @staticmethod
    def _rollback_rules(rules, macvlan_interface=None, bridge_interface=None):
        """Attempt to remove rules that were applied
        
        Args:
            rules: List of rule strings to remove
            macvlan_interface: Macvlan interface name (optional)
            bridge_interface: Bridge interface name (optional)
        """
        # Whitelist of allowed tables and chains for security
        ALLOWED_TABLES = {'nat', 'filter'}
        ALLOWED_CHAINS = {'PREROUTING', 'POSTROUTING', 'DOCKER-USER', 'FORWARD'}
        
        for rule_str in rules:
            try:
                # Validate rule format: must have exactly 3 parts (table, chain, rule)
                parts = rule_str.split(' ', 2)
                if len(parts) != 3:
                    logger.warning(f"Invalid rule format (expected 3 parts): {rule_str}")
                    continue
                
                table = parts[0].strip()
                chain = parts[1].strip()
                rule = parts[2].strip()
                
                # Validate table and chain against whitelist
                if table not in ALLOWED_TABLES:
                    logger.warning(f"Invalid table in rule: {table}")
                    continue
                
                if chain not in ALLOWED_CHAINS:
                    logger.warning(f"Invalid chain in rule: {chain}")
                    continue
                
                # Validate rule components - split and validate each part
                rule_parts = rule.split()
                # Basic validation: ensure no shell metacharacters
                for part in rule_parts:
                    if any(char in part for char in [';', '&', '|', '`', '$', '(', ')', '<', '>', '\n', '\r']):
                        logger.warning(f"Potentially unsafe character in rule part: {part}")
                        raise ValueError("Unsafe character detected")
                
                # Build command safely
                if table == 'nat':
                    cmd = ['iptables', '-t', 'nat', '-D', chain] + rule_parts
                else:
                    # For filter table (includes DOCKER-USER and FORWARD chains)
                    cmd = ['iptables', '-D', chain] + rule_parts
                
                subprocess.run(cmd, capture_output=True)
                
            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse or validate rule '{rule_str}': {e}")
                continue
            except Exception as e:
                # Best effort rollback - rules may not exist
                logger.debug(f"Failed to remove rule '{rule_str}': {e}")
                pass
    
    @staticmethod
    def remove_nat_rules(src_ip, dst_ip, macvlan_interface, bridge_interface, parent_interface='ens18'):
        """Remove all 5 NAT rules for given IP pair
        
        Args:
            src_ip: External macvlan IP address
            dst_ip: Internal Docker container IP
            macvlan_interface: Macvlan interface name
            bridge_interface: Docker bridge interface
            parent_interface: Parent interface (default: ens18)
            
        Returns:
            dict: Result with success status
        """
        try:
            logger.info(f"Removing NAT rules for {src_ip} → {dst_ip}")
            
            removed = 0
            
            # Remove Rule 1: DNAT
            try:
                subprocess.run(
                    ['iptables', '-t', 'nat', '-D', 'PREROUTING', '-d', src_ip, '-j', 'DNAT', '--to-destination', dst_ip],
                    check=True,
                    capture_output=True
                )
                removed += 1
                logger.info(f"✓ Removed DNAT rule")
            except Exception as e:
                logger.debug(f"Failed to remove DNAT rule (may not exist): {e}")
            
            # Remove Rule 2: SNAT
            try:
                subprocess.run(
                    ['iptables', '-t', 'nat', '-D', 'POSTROUTING', '-s', dst_ip, '-j', 'SNAT', '--to-source', src_ip],
                    check=True,
                    capture_output=True
                )
                removed += 1
                logger.info(f"✓ Removed SNAT rule")
            except Exception as e:
                logger.debug(f"Failed to remove SNAT rule (may not exist): {e}")
            
            # Remove Rule 3: DOCKER-USER incoming
            try:
                subprocess.run(
                    ['iptables', '-D', 'DOCKER-USER', '-d', dst_ip, '-j', 'ACCEPT'],
                    check=True,
                    capture_output=True
                )
                removed += 1
                logger.info(f"✓ Removed DOCKER-USER incoming rule")
            except Exception as e:
                logger.debug(f"Failed to remove DOCKER-USER incoming rule (may not exist): {e}")
            
            # Remove Rule 4: DOCKER-USER outgoing via macvlan0
            try:
                subprocess.run(
                    ['iptables', '-D', 'DOCKER-USER', '-o', macvlan_interface, '-s', dst_ip, '-j', 'ACCEPT'],
                    check=True,
                    capture_output=True
                )
                removed += 1
                logger.info(f"✓ Removed DOCKER-USER outgoing rule")
            except Exception as e:
                logger.debug(f"Failed to remove DOCKER-USER outgoing rule (may not exist): {e}")
            
            # Remove Rule 5: FORWARD from bridge (THE CRITICAL FIX)
            try:
                subprocess.run(
                    ['iptables', '-D', 'FORWARD', '-i', bridge_interface, '-s', dst_ip, '-j', 'ACCEPT'],
                    check=True,
                    capture_output=True
                )
                removed += 1
                logger.info(f"✓ Removed FORWARD rule")
            except Exception as e:
                logger.debug(f"Failed to remove FORWARD rule (may not exist): {e}")
            
            logger.info(f"Removed {removed}/5 NAT rules for {src_ip} → {dst_ip}")
            
            return {
                'success': True,
                'removed': removed,
                'message': f'Removed {removed}/5 NAT rules'
            }
            
        except Exception as e:
            logger.error(f"Error removing NAT rules: {e}")
            return {
                'success': False,
                'removed': 0,
                'message': f'Error removing NAT rules: {str(e)}'
            }
    
    @staticmethod
    def check_port_conflicts(ip_address):
        """Check if IP address has ports that might conflict with webserver
        
        Args:
            ip_address: IP address to check
            
        Returns:
            dict: Result with conflicts list and warning message
        """
        conflicts = []
        webserver_ports = [80, 443, 8000, 8080, 8443]
        
        try:
            # Check if any of the webserver ports are listening on this IP
            result = subprocess.run(
                ['ss', '-tlnp'],
                capture_output=True,
                text=True,
                check=True
            )
            
            for line in result.stdout.splitlines():
                for port in webserver_ports:
                    if f'{ip_address}:{port}' in line or f'0.0.0.0:{port}' in line or f'*:{port}' in line:
                        if port not in conflicts:
                            conflicts.append(port)
            
            if conflicts:
                return {
                    'has_conflicts': True,
                    'conflicts': conflicts,
                    'message': f'Warning: Ports {conflicts} may conflict with services on {ip_address}'
                }
            else:
                return {
                    'has_conflicts': False,
                    'conflicts': [],
                    'message': 'No port conflicts detected'
                }
                
        except Exception as e:
            logger.warning(f"Could not check port conflicts: {e}")
            return {
                'has_conflicts': False,
                'conflicts': [],
                'message': 'Could not verify port conflicts'
            }
    
    @staticmethod
    def list_active_rules():
        """List all active iptables NAT and FORWARD rules
        
        Returns:
            dict: Result with list of rules
        """
        try:
            # Get NAT rules
            nat_result = subprocess.run(
                ['iptables', '-t', 'nat', '-L', '-n', '-v'],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Get FORWARD rules
            forward_result = subprocess.run(
                ['iptables', '-L', 'FORWARD', '-n', '-v'],
                capture_output=True,
                text=True,
                check=True
            )
            
            return {
                'success': True,
                'nat_rules': nat_result.stdout,
                'forward_rules': forward_result.stdout
            }
            
        except Exception as e:
            logger.error(f"Failed to list iptables rules: {e}")
            return {
                'success': False,
                'nat_rules': '',
                'forward_rules': '',
                'message': f'Error: {str(e)}'
            }
    
    @staticmethod
    def get_bridge_interface_from_network(docker_network_id):
        """Get bridge interface name from Docker network ID
        
        Args:
            docker_network_id: Docker network ID
            
        Returns:
            str: Bridge interface name (e.g., br-431706091c0d) or None
        """
        try:
            # Docker creates bridge interface with name: br-<first 12 chars of network ID>
            if docker_network_id and len(docker_network_id) >= 12:
                bridge_name = f"br-{docker_network_id[:12]}"
                
                # Verify interface exists
                result = subprocess.run(
                    ['ip', 'link', 'show', bridge_name],
                    capture_output=True,
                    text=True
                )
                
                if result.returncode == 0:
                    logger.info(f"Found bridge interface: {bridge_name}")
                    return bridge_name
                else:
                    logger.warning(f"Bridge interface {bridge_name} not found")
                    return None
            else:
                logger.error(f"Invalid docker network ID: {docker_network_id}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting bridge interface: {e}")
            return None
    
    @staticmethod
    def test_nat_configuration():
        """Test if iptables is accessible and properly configured
        
        Returns:
            dict: Test result with success status
        """
        try:
            # Check if iptables is available
            subprocess.run(
                ['which', 'iptables'],
                capture_output=True,
                check=True
            )
            
            # Check if we can list rules (requires root/CAP_NET_ADMIN)
            subprocess.run(
                ['iptables', '-L', '-n'],
                capture_output=True,
                check=True
            )
            
            return {
                'success': True,
                'message': 'iptables is available and accessible'
            }
            
        except subprocess.CalledProcessError:
            return {
                'success': False,
                'message': 'iptables is not accessible (requires root privileges)'
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Error testing iptables: {str(e)}'
            }



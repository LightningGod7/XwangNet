import requests
import json
import uuid
import logging

logger = logging.getLogger(__name__)

class ProxyManager:
    CADDY_ADMIN_URL = "http://localhost:2019"

    @staticmethod
    def generate_webtop_hostname():
        hostname = f"{uuid.uuid4().hex[:8]}.webtop.localhost"
        logger.info(f"Generated hostname: {hostname}")
        return hostname

    @staticmethod
    def add_webtop_proxy(hostname, container_ip, container_port):
        try:
            logger.info(f"Adding proxy for {hostname} -> {container_ip}:{container_port}")

            # Configure the route
            route_config = {
                "@id": hostname,
                "match": [{"host": [hostname]}],
                "handle": [{
                    "handler": "reverse_proxy",
                    "upstreams": [{"dial": f"{container_ip}:{container_port}"}]
                }],
                "terminal": True  # Add this to prevent fallthrough to other routes
            }

            logger.info(f"Adding route config: {json.dumps(route_config, indent=2)}")

            # Add the route to Caddy
            response = requests.post(
                f"{ProxyManager.CADDY_ADMIN_URL}/config/apps/http/servers/srv0/routes",
                json=route_config
            )
            
            if response.status_code == 200:
                logger.info(f"Successfully added proxy for {hostname} -> {container_ip}:{container_port}")
                
                # Verify the config was added
                new_config = requests.get(f"{ProxyManager.CADDY_ADMIN_URL}/config/").json()
                logger.debug(f"New Caddy config: {json.dumps(new_config, indent=2)}")
                
                return True
            else:
                logger.error(f"Failed to add proxy. Status: {response.status_code}, Response: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Error adding proxy: {str(e)}")
            return False

    @staticmethod
    def remove_webtop_proxy(hostname):
        try:
            logger.info(f"Removing proxy for {hostname}")
            response = requests.delete(
                f"{ProxyManager.CADDY_ADMIN_URL}/id/{hostname}"
            )
            success = response.status_code == 200
            if success:
                logger.info(f"Successfully removed proxy for {hostname}")
            else:
                logger.error(f"Failed to remove proxy for {hostname}. Status: {response.status_code}. Response: {response.text}")
            return success
        except Exception as e:
            logger.error(f"Error removing proxy: {str(e)}")
            return False 
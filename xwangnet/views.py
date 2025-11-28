from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import  DeviceTemplate, NetworkConfiguration, Deployment, DeployedContainer
from .forms import NetworkConfigurationForm, ComposeGeneratorForm
import docker
import yaml
from django.http import JsonResponse, HttpResponse
import json
from django.template.loader import render_to_string
from django.core.files.storage import FileSystemStorage
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
import re
from concurrent.futures import ThreadPoolExecutor
import os
from channels.generic.websocket import AsyncWebsocketConsumer
import asyncio
import paramiko
import docker.errors
from xwangnet.services.proxy_manager import ProxyManager
import time
import logging
import subprocess
import ipaddress

client = docker.from_env()
logger = logging.getLogger(__name__)

def network_list(request):
    #networks = DockerNetwork.objects.all()
    networks = client.networks.list(greedy=True)
    networks_data = []
    for network in networks:
        containers = []
        for container_id, container_attrs in network.attrs.get('Containers', {}).items():
            containers.append({
                'id': container_id,
                'name': container_attrs.get('Name'),
                'mac_address': container_attrs.get('MacAddress'),
                'ipv4_address': container_attrs.get('IPv4Address'),
            })
        networks_data.append({
            'name': network.name,
            'short_id': network.short_id,
            'containers': containers,
        })
    return render(request, 'network_list.html', {'networks': networks_data})

def container_list(request):
    return render(request, 'container_list.html', {
        'initial_load': True
    })

def get_container_stats(container):
    try:
        container_stats = container.stats(stream=False)
        cpu_usage, mem_usage, mem_limit, mem_percent = 0, 0, 0, 0

        # CPU Usage Calculation
        cpu_delta = container_stats['cpu_stats']['cpu_usage']['total_usage'] - \
                    container_stats['precpu_stats']['cpu_usage']['total_usage']
        system_delta = container_stats['cpu_stats']['system_cpu_usage'] - \
                       container_stats['precpu_stats']['system_cpu_usage']
        num_cpus = container_stats['cpu_stats'].get('online_cpus', 1)

        if system_delta > 0:
            cpu_usage = (cpu_delta / system_delta) * 100 * num_cpus

        # Memory Usage Calculation
        mem_usage = container_stats['memory_stats'].get('usage', 0)
        mem_limit = container_stats['memory_stats'].get('limit', 1)  # Avoid zero division
        mem_percent = (mem_usage / mem_limit) * 100

        return {
            'cpu_usage': round(min(max(cpu_usage, 0), 100), 2),
            'mem_usage': round(mem_usage / (1024 * 1024), 2),
            'mem_limit': round(mem_limit / (1024 * 1024), 2),
            'mem_percent': round(mem_percent, 2)
        }
    except Exception:
        return {'cpu_usage': 0, 'mem_usage': 0, 'mem_limit': 0, 'mem_percent': 0}

def container_list_api(request):
    deployments = Deployment.objects.prefetch_related('containers')
    docker_containers = {c.id: c for c in client.containers.list(all=True)}

    result = {}
    container_stats_map = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_container = {executor.submit(get_container_stats, container): container.id 
                               for container in docker_containers.values()}
        for future in future_to_container:
            container_id = future_to_container[future]
            container_stats_map[container_id] = future.result()

    for deployment in deployments:
        containers_data = []
        for deployed_container in deployment.containers.all():
            container = docker_containers.get(deployed_container.container_id)
            if not container:
                continue

            stats = container_stats_map.get(container.id, {'cpu_usage': 0, 'mem_usage': 0, 'mem_limit': 0, 'mem_percent': 0})
            
            container_info = {
                'id': container.short_id,
                'name': deployed_container.hostname,
                'status': container.status,
                'image': container.image.tags[0] if container.image.tags else container.image.short_id,
                'created': container.attrs['Created'],
                'ports': container.ports,
                'network': deployment.network.name if deployment.network else None,
                'health_status': container.attrs.get('State', {}).get('Health', {}).get('Status', 'No health check'),
                'health_log': container.attrs.get('State', {}).get('Health', {}).get('Log', [])[-3:] if isinstance(container.attrs.get('State', {}).get('Health', {}).get('Log', []), list) else ['No health logs'],
                'device_name': deployed_container.device.name,
                'is_isolated': deployment.network.isolated if deployment.network else None,
                **stats
            }
            containers_data.append(container_info)

        if containers_data:
            result[deployment.id] = {
                'name': deployment.name,
                'network_status': deployment.network_status,
                'containers': containers_data
            }

    running_count = sum(1 for deployment in result.values() for c in deployment['containers'] if c['status'] == 'running')
    total_count = sum(len(deployment['containers']) for deployment in result.values())

    return JsonResponse({
        'status': 'success',
        'data': {
            'deployments': result,
            'running_count': running_count,
            'total_count': total_count
        }
    })

def device_selection(request):
    if request.method == 'POST':
        form = ComposeGeneratorForm(request.POST)
        if form.is_valid():
            device_counts = form.cleaned_data['device_counts']
            selected_devices = []
            
            for device_id, count in device_counts.items():
                device = DeviceTemplate.objects.get(id=device_id)
                for i in range(count):
                    # Match frontend naming: device-1, device-2, etc. (not device-version-1)
                    device_name = f"{device.name}-{i+1}" if count > 1 else device.name
                    selected_devices.append({
                        'id': device.id,
                        'name': device_name,
                        'original_name': device.name
                    })
            
            # Store edge device designation
            edge_device = request.POST.get('edge_device', '')
            request.session['selected_devices'] = selected_devices
            request.session['edge_device'] = edge_device
            return redirect('network_config')
    else:
        form = ComposeGeneratorForm()
    
    return render(request, 'device_selection.html', {'form': form})

def network_config(request):
    if request.method == 'POST':
        form = NetworkConfigurationForm(request.POST)
        if form.is_valid():
            network = form.save(commit=False)
            
            # Handle external IP configuration (non-isolated mode)
            if not network.isolated:
                interface_option = request.POST.get('interface_option')
                
                # Validate that interface_option is provided
                if not interface_option or interface_option not in ['existing', 'new']:
                    messages.error(request, 'Please select an interface configuration option (existing or new interface)')
                    return render(request, 'network_config.html', {'form': form})
                
                # Check if edge device is designated
                edge_device = request.session.get('edge_device', '')
                if not edge_device:
                    messages.warning(request, 'No edge device selected. External IP binding requires an edge device designation.')
                
                if interface_option == 'existing':
                    # Use existing interface
                    external_interface_json = request.POST.get('external_interface')
                    port_forwarding = request.POST.get('port_forwarding', '').strip()
                    
                    if external_interface_json:
                        try:
                            iface_data = json.loads(external_interface_json)
                            selected_ip = iface_data.get('ip')
                            
                            # CRITICAL: Validate against server's IP to prevent SSH/webserver disconnection
                            from xwangnet.services.interface_manager import InterfaceManager
                            server_interfaces = InterfaceManager.list_available_interfaces()
                            server_ips = [iface['ip'] for iface in server_interfaces]
                            
                            if selected_ip in server_ips:
                                # Allow if port forwarding is specified, otherwise block
                                if not port_forwarding:
                                    messages.error(
                                        request, 
                                        f'❌ CRITICAL: Cannot use server IP {selected_ip} without port forwarding! '
                                        f'This will disconnect SSH and webserver. Please specify port forwarding or create a new interface.'
                                    )
                                    return render(request, 'network_config.html', {'form': form})
                                else:
                                    messages.warning(
                                        request,
                                        f'⚠️ Using server IP {selected_ip} with port forwarding. Ensure SSH (22) and webserver (8000) ports are not forwarded!'
                                    )
                            
                            network.external_interface = iface_data.get('interface')
                            network.external_ip = selected_ip
                            network.use_external_ip = True
                            network.create_new_interface = False
                            network.port_forwarding = port_forwarding
                        except json.JSONDecodeError:
                            messages.error(request, 'Invalid interface selection')
                            return render(request, 'network_config.html', {'form': form})
                    else:
                        messages.error(request, 'Please select an interface')
                        return render(request, 'network_config.html', {'form': form})
                
                elif interface_option == 'new':
                    # Create new interface
                    ip_assignment = request.POST.get('ip_assignment')
                    network.create_new_interface = True
                    network.use_external_ip = True
                    
                    if ip_assignment == 'dhcp':
                        network.use_dhcp = True
                    elif ip_assignment == 'manual':
                        network.use_dhcp = False
                        manual_ip = request.POST.get('external_ip')
                        if manual_ip:
                            network.external_ip = manual_ip
                        else:
                            messages.error(request, 'Manual IP address is required')
                            return render(request, 'network_config.html', {'form': form})
            else:
                # Isolated mode - clear external IP settings
                network.use_external_ip = False
                network.create_new_interface = False
                network.external_interface = None
                network.external_ip = None
                
                # Auto-add webtop for isolated networks
                try:
                    webtop_device = DeviceTemplate.objects.filter(name__iexact='webtop').first()
                    if webtop_device:
                        selected_devices = request.session.get('selected_devices', [])
                        # Check if webtop is not already in the list
                        if not any(d['original_name'].lower() == 'webtop' for d in selected_devices):
                            selected_devices.append({
                                'id': webtop_device.id,
                                'name': 'webtop',
                                'original_name': 'webtop'
                            })
                            request.session['selected_devices'] = selected_devices
                            logger.info("✓ Auto-added webtop for isolated network")
                except Exception as e:
                    logger.warning(f"Failed to auto-add webtop: {str(e)}")
            
            network.save()
            request.session['network_id'] = network.id
            return redirect('compose_preview')
        else:
            # Form is invalid, show errors
            messages.error(request, 'Please correct the errors below.')
    else:
        form = NetworkConfigurationForm()
    return render(request, 'network_config.html', {'form': form})

def compose_preview(request):
    network = NetworkConfiguration.objects.get(id=request.session['network_id'])
    selected_devices = request.session['selected_devices']
    edge_device = request.session.get('edge_device', '')
    
    compose_data = {
        'version': '3.9',
        'networks': {
            network.name: {
                'driver': 'bridge',
                'ipam': {
                    'config': [{'subnet': network.subnet, 'gateway': network.gateway}]
                }
            }
        },
        'services': {}
    }
    
    # Get all unique device templates first
    device_ids = [device['id'] for device in selected_devices]
    devices = DeviceTemplate.objects.filter(id__in=device_ids)
    device_map = {device.id: device for device in devices}
    
    # Create services for each selected device instance
    for device_info in selected_devices:
        device = device_map[device_info['id']]
        service_name = device_info['name']
        
        compose_data['services'][service_name] = {
            'image': device.image,
            'networks': [network.name],
            'hostname': service_name,
            'ports': device.ports,
            'environment': device.environment
        }
    
    yaml_content = yaml.dump(compose_data, default_flow_style=False)
    return render(request, 'compose_preview.html', {
        'network': network,
        'selected_devices': selected_devices,
        'edge_device': edge_device
    })

def deploy_compose(request):
    if request.method == 'POST':
        try:
            name = request.POST.get('deployment_name')
            description = request.POST.get('deployment_description')
            
            if not name or not description:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Deployment name and description are required'
                }, status=400)
            
            network = NetworkConfiguration.objects.get(id=request.session['network_id'])
            selected_devices = request.session['selected_devices']
            edge_device = request.session.get('edge_device', '')
            
            # Create deployment record
            deployment = Deployment.objects.create(
                name=name,
                description=description,
                network=network,
                network_status='down'
            )
            
            # Get all device templates
            device_ids = [device['id'] for device in selected_devices]
            devices = DeviceTemplate.objects.filter(id__in=device_ids)
            device_map = {device.id: device for device in devices}
            
            # Create container records for each instance
            for device_info in selected_devices:
                device = device_map[device_info['id']]
                is_edge = (device_info['name'] == edge_device)
                
                DeployedContainer.objects.create(
                    deployment=deployment,
                    device=device,
                    hostname=device_info['name'],
                    status='stopped',
                    is_edge_device=is_edge
                )
            
            return JsonResponse({
                'status': 'success',
                'message': 'Deployment created successfully',
                'deployment_id': deployment.id
            })
            
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

def home(request):
    return render(request, 'home.html')

def deployment_list(request):
    deployments = Deployment.objects.all().order_by('-created_at')
    return render(request, 'deployment_list.html', {
        'deployments': deployments
    })

def deployment_detail(request, deployment_id):
    deployment = get_object_or_404(Deployment, id=deployment_id)
    
    if request.method == 'DELETE':
        try:
            # Step 1: Stop all containers in this deployment
            for container in deployment.containers.all():
                if container.container_id:
                    try:
                        docker_container = client.containers.get(container.container_id)
                        docker_container.stop(timeout=10)
                        docker_container.remove()
                        logger.info(f"✓ Stopped and removed container {container.hostname}")
                    except docker.errors.NotFound:
                        logger.info(f"Container {container.hostname} already removed")
                    except Exception as e:
                        logger.warning(f"Failed to stop container {container.hostname}: {str(e)}")
            
            # Step 2: Stop Suricata container
            if deployment.suricata_container_id:
                try:
                    suricata_container = client.containers.get(deployment.suricata_container_id)
                    suricata_container.stop(timeout=10)
                    suricata_container.remove()
                    logger.info("✓ Stopped and removed Suricata container")
                except docker.errors.NotFound:
                    logger.info("Suricata container already removed")
                except Exception as e:
                    logger.warning(f"Failed to stop Suricata: {str(e)}")
            
            # Step 3: Clean up macvlan interface and NAT rules if they exist
            if deployment.network.use_external_ip and deployment.network.external_interface:
                from xwangnet.services.interface_manager import InterfaceManager
                from xwangnet.services.nat_manager import NATManager
                from xwangnet.models import EdgeDeviceNATRule
                
                # Get bridge interface from network ID
                bridge_interface = NATManager.get_bridge_interface_from_network(deployment.docker_network_id)
                
                # Remove all NAT rules for this deployment
                nat_rules = EdgeDeviceNATRule.objects.filter(deployment=deployment)
                for rule in nat_rules:
                    if rule.active and bridge_interface:
                        NATManager.remove_nat_rules(
                            src_ip=rule.lan_ip,
                            dst_ip=rule.internal_ip,
                            macvlan_interface=rule.macvlan_interface,
                            bridge_interface=bridge_interface
                        )
                        logger.info(f"✓ Removed NAT rules for {rule.lan_ip} → {rule.internal_ip}")
                    rule.delete()
                
                # Release DHCP and delete interface
                if deployment.network.create_new_interface:
                    if deployment.network.use_dhcp:
                        InterfaceManager.release_dhcp_ip(deployment.network.external_interface)
                    InterfaceManager.delete_interface(deployment.network.external_interface)
                    logger.info(f"✓ Deleted macvlan interface {deployment.network.external_interface}")
            
            # Step 4: Remove Docker networks
            if deployment.docker_network_id:
                try:
                    network = client.networks.get(deployment.docker_network_id)
                    network.remove()
                    logger.info(f"✓ Removed Docker network {deployment.network.name}")
                except docker.errors.NotFound:
                    logger.info("Docker network already removed")
                except Exception as e:
                    logger.warning(f"Failed to remove Docker network: {str(e)}")
            
            # Step 5: Clean up webtop network
            cleanup_webtop_network(deployment_id)
            
            # Step 6: Store network reference before deleting deployment
            network = deployment.network
            
            # Step 7: Delete deployment from database
            deployment.delete()
            
            # Step 8: Delete network if no more deployments use it
            if not network.deployment_set.exists():
                logger.info(f"✓ No more deployments using network {network.name}, deleting network configuration")
                network.delete()
            
            return JsonResponse({'status': 'success', 'message': 'Deployment deleted successfully'})
            
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': f'Failed to delete deployment: {str(e)}'
            }, status=500)
    
    return render(request, 'deployment_detail.html', {
        'deployment': deployment,
        'deployments': Deployment.objects.all().order_by('-created_at'),
        'device_templates': DeviceTemplate.objects.all(),
        'monitoring_enabled': deployment.suricata_status == 'active'
    })

def add_containers_to_deployment(request, deployment_id):
    if request.method == 'POST':
        try:
            deployment = get_object_or_404(Deployment, id=deployment_id)
            data = json.loads(request.body)
            device_counts = data.get('device_counts', {})
            
            for device_id, count in device_counts.items():
                device = DeviceTemplate.objects.get(id=device_id)
                for i in range(count):
                    # Generate unique hostname
                    base_hostname = f"{device.name}-{device.version}"
                    existing_count = DeployedContainer.objects.filter(
                        deployment=deployment,
                        hostname__startswith=base_hostname
                    ).count()
                    hostname = f"{base_hostname}-{existing_count + 1}"

                    DeployedContainer.objects.create(
                        deployment=deployment,
                        device=device,
                        hostname=hostname,
                        status='stopped'
                    )
            
            return JsonResponse({
                'status': 'success',
                'message': 'Containers added successfully'
            })
            
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
            
    return JsonResponse({
        'status': 'error',
        'message': 'Method not allowed'
    }, status=405)

def create_deployment(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description')
        network = NetworkConfiguration.objects.get(id=request.session['network_id'])
        devices = DeviceTemplate.objects.filter(id__in=request.session['selected_devices'])
        
        deployment = Deployment.objects.create(
            name=name,
            description=description,
            network=network
        )
        
        for device in devices:
            DeployedContainer.objects.create(
                deployment=deployment,
                device=device,
                hostname=f"{device.name}-{network.name}"
            )
        
        return JsonResponse({'status': 'success', 'deployment_id': deployment.id})
    return JsonResponse({'status': 'error'}, status=400)

def toggle_network(request, deployment_id):
    deployment = get_object_or_404(Deployment, id=deployment_id)
    data = json.loads(request.body)
    action = data.get('action')
    
    try:
        if action == 'up' and deployment.network_status == 'down':
            try:
                # Check if network already exists
                existing_networks = client.networks.list(names=[deployment.network.name])
                if existing_networks:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Network "{deployment.network.name}" already exists'
                    }, status=400)
                
                # Check if subnet and gateway are provided
                network_params = {
                    'name': deployment.network.name,
                    'driver': 'bridge',
                    'internal': deployment.network.isolated
                }

                # Only add IPAM config if both subnet and gateway are provided
                if deployment.network.subnet and deployment.network.gateway:
                    network_params['ipam'] = docker.types.IPAMConfig(
                        pool_configs=[
                            docker.types.IPAMPool(
                                subnet=deployment.network.subnet,
                                gateway=deployment.network.gateway
                            )
                        ]
                    )
                
                # Try to create the network with or without IPAM config
                network = client.networks.create(**network_params)
                deployment.docker_network_id = network.id
                deployment.network_status = 'up'
                deployment.save()
                
                # Inspect the network to get the actual subnet assigned by Docker
                network.reload()
                if network.attrs.get('IPAM') and network.attrs['IPAM'].get('Config'):
                    ipam_config = network.attrs['IPAM']['Config']
                    if ipam_config and len(ipam_config) > 0:
                        actual_subnet = ipam_config[0].get('Subnet')
                        actual_gateway = ipam_config[0].get('Gateway')
                        
                        # Update the network configuration if subnet was not set
                        if not deployment.network.subnet and actual_subnet:
                            deployment.network.subnet = actual_subnet
                            deployment.network.gateway = actual_gateway
                            deployment.network.save()
                            logger.info(f"✓ Populated network subnet: {actual_subnet}, gateway: {actual_gateway}")
                
                # NEW: Create macvlan interface and configure external IP if needed
                if deployment.network.use_external_ip and deployment.network.create_new_interface:
                    try:
                        from xwangnet.services.interface_manager import InterfaceManager
                        
                        # Find the edge device to name the interface after it
                        edge_device = deployment.containers.filter(is_edge_device=True).first()
                        if edge_device:
                            # Clean device name for interface (remove spaces, special chars)
                            clean_name = re.sub(r'[^a-z0-9]', '', edge_device.device.name.lower())
                            interface_name = clean_name[:15]  # Limit to 15 chars for interface name
                            logger.info(f"Creating interface named after edge device: {interface_name}")
                        else:
                            # Fallback to auto-generated name if no edge device found yet
                            interface_name = None
                        
                        # Create macvlan interface
                        interface_name = InterfaceManager.create_macvlan_interface(name=interface_name)
                        
                        # Request DHCP IP or assign static
                        if deployment.network.use_dhcp:
                            ip_address = InterfaceManager.request_dhcp_ip(interface_name)
                            if not ip_address:
                                # DHCP failed, clean up
                                InterfaceManager.delete_interface(interface_name)
                                return JsonResponse({
                                    'status': 'error',
                                    'message': 'Failed to obtain DHCP IP address. Please try manual IP assignment.'
                                }, status=500)
                        else:
                            ip_address = deployment.network.external_ip
                            if not InterfaceManager.assign_static_ip(interface_name, ip_address):
                                # Static IP failed, clean up
                                InterfaceManager.delete_interface(interface_name)
                                return JsonResponse({
                                    'status': 'error',
                                    'message': f'Failed to assign static IP {ip_address}'
                                }, status=500)
                        
                        # Update database with interface info
                        deployment.network.external_interface = interface_name
                        deployment.network.external_ip = ip_address
                        deployment.network.save()
                        
                        logger.info(f"✓ Created macvlan interface {interface_name} with IP {ip_address}")
                        
                    except Exception as e:
                        # Rollback network creation if interface setup fails
                        network.remove()
                        deployment.docker_network_id = None
                        deployment.network_status = 'down'
                        deployment.save()
                        logger.error(f"Failed to create macvlan interface: {str(e)}")
                        return JsonResponse({
                            'status': 'error',
                            'message': f'Failed to create macvlan interface: {str(e)}'
                        }, status=500)
                
            except docker.errors.APIError as e:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Docker API Error: {str(e)}'
                }, status=500)
                
        elif action == 'down' and deployment.network_status == 'up':
            try:
                # NEW: Clean up macvlan interface and NAT rules if they exist
                if deployment.network.use_external_ip and deployment.network.external_interface:
                    from xwangnet.services.interface_manager import InterfaceManager
                    from xwangnet.services.nat_manager import NATManager
                    from xwangnet.models import EdgeDeviceNATRule
                    
                    # Get bridge interface from network ID
                    bridge_interface = NATManager.get_bridge_interface_from_network(deployment.docker_network_id)
                    
                    # Remove all NAT rules for this deployment
                    nat_rules = EdgeDeviceNATRule.objects.filter(deployment=deployment, active=True)
                    for rule in nat_rules:
                        if bridge_interface:
                            NATManager.remove_nat_rules(
                                src_ip=rule.lan_ip,
                                dst_ip=rule.internal_ip,
                                macvlan_interface=rule.macvlan_interface,
                                bridge_interface=bridge_interface
                            )
                        rule.active = False
                        rule.save()
                        logger.info(f"✓ Removed NAT rules for {rule.lan_ip} → {rule.internal_ip}")
                    
                    # Release DHCP and delete interface
                    if deployment.network.create_new_interface:
                        if deployment.network.use_dhcp:
                            InterfaceManager.release_dhcp_ip(deployment.network.external_interface)
                        InterfaceManager.delete_interface(deployment.network.external_interface)
                        logger.info(f"✓ Deleted macvlan interface {deployment.network.external_interface}")
                
                # Remove Docker network
                network = client.networks.get(deployment.docker_network_id)
                network.remove()
                deployment.docker_network_id = None
                deployment.network_status = 'down'
                deployment.save()
                
            except docker.errors.NotFound:
                # Network already removed, just update the status
                deployment.docker_network_id = None
                deployment.network_status = 'down'
                deployment.save()
                
            except docker.errors.APIError as e:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Failed to remove network: {str(e)}'
                }, status=500)
            
        return JsonResponse({
            'status': 'success',
            'network_status': deployment.network_status
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': f'Unexpected error: {str(e)}'
        }, status=500)

def ensure_webtop_network(deployment_id):
    """Ensures the deployment-specific webtop network exists and is properly configured"""
    webtop_network_name = f'webtop-network-{deployment_id}'
    try:
        webtop_network = client.networks.get(webtop_network_name)
    except docker.errors.NotFound:
        webtop_network = client.networks.create(
            webtop_network_name,
            driver='bridge',
            internal=False,
            attachable=True,
            options={
                "com.docker.network.bridge.name": f"webtop-br-{deployment_id}",
                "com.docker.network.bridge.enable_ip_masquerade": "true",
                "com.docker.network.bridge.enable_icc": "true"
            },
            ipam=docker.types.IPAMConfig(
                pool_configs=[
                    docker.types.IPAMPool(
                        subnet=f'172.20.{deployment_id}.0/24',  # Unique subnet per deployment
                        gateway=f'172.20.{deployment_id}.1'
                    )
                ]
            )
        )
    return webtop_network

def container_action(request, container_id):
    container = get_object_or_404(DeployedContainer, id=container_id)
    data = json.loads(request.body)
    action = data.get('action')
    
    try:
        if action == 'start' and container.deployment.network_status == 'up':
            # Get image ID
            try:
                image = client.images.get(container.device.image)
                image_id = image.id
            except docker.errors.ImageNotFound:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Image {container.device.image} not found'
                }, status=404)
            
            network_config = {}
            
            # Special handling for webtop containers
            if container.device.name == 'webtop':
                # Get deployment-specific webtop network
                webtop_network = ensure_webtop_network(container.deployment.id)
                webtop_network_name = webtop_network.name
                
                # Webtop only connects to webtop network
                network_config = {
                    webtop_network_name: None  # None means use default network settings
                }
            else:
                # Non-webtop containers just use the deployment network
                network_config = {
                    container.deployment.network.name: None
                }

            # Create container with network configuration
            docker_container = client.containers.run(
                image_id,
                name=f"{container.hostname}-{container.id}",
                hostname=container.hostname,
                network=list(network_config.keys())[0],
                environment=container.device.environment,
                ports=container.device.ports,
                detach=True,
                remove=True
            )

            docker_container.reload()
            container.container_id = docker_container.id
            container.status = 'running'
            container.internal_ip = docker_container.attrs['NetworkSettings']['Networks'].get(list(network_config.keys())[0])['IPAddress']
            container.save()

            # NEW: Configure NAT if this is an edge device
            if container.is_edge_device and container.deployment.network.use_external_ip:
                try:
                    from xwangnet.services.nat_manager import NATManager
                    from xwangnet.models import EdgeDeviceNATRule
                    
                    external_ip = container.deployment.network.external_ip
                    internal_ip = container.internal_ip
                    macvlan_interface = container.deployment.network.external_interface
                    
                    # Get bridge interface from Docker network
                    bridge_interface = NATManager.get_bridge_interface_from_network(
                        container.deployment.docker_network_id
                    )
                    
                    if external_ip and internal_ip and macvlan_interface and bridge_interface:
                        logger.info(f"Configuring NAT for edge device: {external_ip} → {internal_ip}")
                        logger.info(f"  Macvlan: {macvlan_interface}, Bridge: {bridge_interface}")
                        
                        # Configure all 5 iptables rules (DNAT, SNAT, DOCKER-USER x2, FORWARD)
                        result = NATManager.configure_full_port_nat(
                            src_ip=external_ip,
                            dst_ip=internal_ip,
                            macvlan_interface=macvlan_interface,
                            bridge_interface=bridge_interface
                        )
                        
                        if result['success']:
                            # Create or update NAT rule record
                            EdgeDeviceNATRule.objects.update_or_create(
                                deployment=container.deployment,
                                edge_container=container,
                                defaults={
                                    'macvlan_interface': macvlan_interface,
                                    'lan_ip': external_ip,
                                    'internal_ip': internal_ip,
                                    'iptables_rules': result['rules'],
                                    'active': True
                                }
                            )
                            
                            container.edge_accessible = True
                            container.save()
                            
                            logger.info(f"✓ Configured NAT with all 5 rules: {external_ip} → {internal_ip}")
                            logger.info(f"  Applied rules: {len(result['rules'])}")
                        else:
                            logger.error(f"✗ Failed to configure NAT: {result.get('message', 'Unknown error')}")
                    else:
                        logger.warning(f"✗ Cannot configure NAT - missing parameters:")
                        logger.warning(f"  external_ip={external_ip}, internal_ip={internal_ip}")
                        logger.warning(f"  macvlan={macvlan_interface}, bridge={bridge_interface}")
                        
                except Exception as e:
                    logger.error(f"Warning: Failed to configure edge device NAT: {str(e)}")

            if container.deployment.suricata_status == 'active':
                try:
                    # Get Suricata IP from the appropriate network
                    suricata_container = client.containers.get(container.deployment.suricata_container_id)
                    if container.device.name == 'webtop':
                        # Get Suricata's IP from webtop network
                        suricata_ip = suricata_container.attrs['NetworkSettings']['Networks'][webtop_network_name]['IPAddress']
                        configure_container_routing(docker_container, suricata_ip)
                    else:
                        # Get Suricata's IP from deployment network
                        suricata_ip = suricata_container.attrs['NetworkSettings']['Networks'][container.deployment.network.name]['IPAddress']
                    configure_container_routing(docker_container, suricata_ip)
                except Exception as e:
                    logger.warning(f"Failed to configure Suricata routing: {str(e)}")

            # Handle webtop proxy if this is a webtop container
            if container.device.name == 'webtop':
                # Poll for container to be ready with timeout
                max_retries = 30  # 30 seconds timeout
                retry_interval = 1  # 1 second between checks
                
                for _ in range(max_retries):
                    docker_container.reload()  # Refresh container info
                    if docker_container.status == 'running':
                        # Check if container is actually responding
                        try:
                            # Get container health status if available
                            health = docker_container.attrs.get('State', {}).get('Health', {}).get('Status')
                            if health == 'healthy' or health is None:  # None means no health check defined
                                break
                        except:
                            pass
                    time.sleep(retry_interval)
                else:  # Loop completed without break - container not ready
                    raise Exception("Container failed to start within timeout period")
                
                docker_container.reload()  # Reload to get fresh container info
                container_info = docker_container.attrs
                network_settings = container_info['NetworkSettings']['Networks']
                network_info = network_settings.get(list(network_config.keys())[0])
                
                if network_info and network_info.get('IPAddress'):  # Make sure we have an IP
                    hostname = ProxyManager.generate_webtop_hostname()
                    container_ip = network_info['IPAddress']
                    
                    logger.debug(f"Container IP: {container_ip}")
                    
                    success = ProxyManager.add_webtop_proxy(
                        hostname,
                        container_ip,  # This should be a valid IP address
                        3000  # Webtop default port
                    )
                    
                    if success:
                        logger.info(f"Successfully added proxy for {hostname} -> {container_ip}:3000")
                        container.hostname = hostname
                        container.save()
                        
                else:
                    logger.debug(f"Network info: {network_info}")
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Could not get container IP address'
                    }, status=500)
            
        elif action == 'stop':
            if container.container_id:
                try:
                    docker_container = client.containers.get(container.container_id)
                    
                    # If it's a webtop container, only disconnect from webtop network
                    if container.device.name == 'webtop':
                        try:
                            webtop_network_name = f'webtop-network-{container.deployment.id}'
                            webtop_network = client.networks.get(webtop_network_name)
                            webtop_network.disconnect(docker_container)
                        except docker.errors.NotFound:
                            pass  # Network might already be gone
                        except docker.errors.APIError as e:
                            logger.warning(f"Failed to disconnect from webtop network: {str(e)}")
                            # Continue with container stop even if network disconnect fails

                    docker_container.stop()
                    
                    # Remove proxy if this is a webtop container
                    if container.device.name == 'webtop' and container.hostname:
                        ProxyManager.remove_webtop_proxy(container.hostname)
                    
                    # Remove NAT rules if this is an edge device
                    if container.is_edge_device:
                        from xwangnet.models import EdgeDeviceNATRule
                        from xwangnet.services.nat_manager import NATManager
                        
                        nat_rules = EdgeDeviceNATRule.objects.filter(edge_container=container, active=True)
                        for nat_rule in nat_rules:
                            # Get bridge interface from docker network ID
                            bridge_interface = NATManager.get_bridge_interface_from_network(
                                container.deployment.docker_network_id
                            )
                            
                            if bridge_interface:
                                result = NATManager.remove_nat_rules(
                                    nat_rule.lan_ip,
                                    nat_rule.internal_ip,
                                    nat_rule.macvlan_interface,
                                    bridge_interface
                                )
                                if result['success']:
                                    nat_rule.active = False
                                    nat_rule.save()
                                    logger.info(f"✓ Removed NAT rules for edge device {container.hostname or container.device.name}")
                                else:
                                    logger.warning(f"Failed to remove NAT rules: {result.get('message', 'Unknown error')}")
                            else:
                                logger.warning(f"Could not find bridge interface for network {container.deployment.docker_network_id}")
                        
                        # Mark edge device as not accessible
                        container.edge_accessible = False
                    
                except docker.errors.NotFound:
                    pass
                container.status = 'stopped'
                container.save()
                
        elif action == 'restart':
            if container.container_id:
                docker_container = client.containers.get(container.container_id)
                docker_container.restart()
                
                # Handle webtop proxy reconfiguration if needed
                if container.device.name == 'webtop':
                    # Remove old proxy
                    if container.hostname:
                        ProxyManager.remove_webtop_proxy(container.hostname)
                    
                # Poll for container to be ready with timeout
                max_retries = 30  # 30 seconds timeout
                retry_interval = 1  # 1 second between checks
                
                container_ready = False
                for _ in range(max_retries):
                    docker_container.reload()  # Refresh container info
                    if docker_container.status == 'running':
                        # Check if container is actually responding
                        try:
                            # Get container health status if available
                            health = docker_container.attrs.get('State', {}).get('Health', {}).get('Status')
                            if health == 'healthy' or health is None:  # None means no health check defined
                                container_ready = True
                                break
                        except:
                            pass
                    time.sleep(retry_interval)
                
                if not container_ready:
                    raise Exception("Container failed to start within timeout period")
                
                # Add new proxy after container is ready (only for webtop)
                if container.device.name == 'webtop':
                    container_info = docker_container.attrs
                    network_settings = container_info['NetworkSettings']['Networks']
                    
                    if network_settings:
                        # Get first available network
                        network_info = list(network_settings.values())[0] if network_settings else None
                        
                        if network_info:
                            hostname = ProxyManager.generate_webtop_hostname()
                            container_ip = network_info['IPAddress']
                            
                            success = ProxyManager.add_webtop_proxy(
                                hostname,
                                container_ip,
                                3000
                            )
                            
                            if success:
                                container.hostname = hostname
                                container.save()
            
        return JsonResponse({
            'status': 'success', 
            'container_status': container.status,
            'container_id': container.container_id,
            'hostname': container.hostname if container.device.name == 'webtop' else None,
            'internal_ip': container.internal_ip
        })
    except docker.errors.APIError as e:
        return JsonResponse({
            'status': 'error',
            'message': f'Docker API Error: {str(e)}'
        }, status=500)
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

def container_logs(request, container_id):
    """Get default container logs (stdout/stderr)"""
    container = get_object_or_404(DeployedContainer, id=container_id)
    
    try:
        if container.container_id and container.status == 'running':
            docker_container = client.containers.get(container.container_id)
            logs = docker_container.logs(tail=100).decode('utf-8')
            return JsonResponse({'status': 'success', 'logs': logs, 'type': 'default'})
        else:
            return JsonResponse({'status': 'error', 'message': 'Container not running'})
    except docker.errors.NotFound:
        return JsonResponse({'status': 'error', 'message': 'Container not found'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

def container_execv_logs(request, container_id):
    """Get execv logs from /xwangnet/execv.log file"""
    container = get_object_or_404(DeployedContainer, id=container_id)
    
    try:
        if container.container_id and container.status == 'running':
            docker_container = client.containers.get(container.container_id)
            
            # Hard-coded log file path: /xwangnet/execv.log
            log_file_path = "/xwangnet/execv.log"
            
            # Use exec to tail the specific file inside the container
            try:
                # Execute tail command inside the container using sh -c
                exec_result = docker_container.exec_run(
                    f'sh -c "tail -n 100 {log_file_path}"',
                    tty=False
                )
                
                if exec_result.exit_code == 0:
                    logs = exec_result.output.decode('utf-8')
                    return JsonResponse({'status': 'success', 'logs': logs, 'type': 'execv'})
                else:
                    # If tail fails, fall back to container logs
                    logs = docker_container.logs(tail=100).decode('utf-8')
                    return JsonResponse({
                        'status': 'success', 
                        'logs': logs,
                        'type': 'execv',
                        'warning': f'Could not tail {log_file_path}, showing container logs instead'
                    })
                    
            except Exception as exec_error:
                # If exec fails, fall back to container logs
                logs = docker_container.logs(tail=100).decode('utf-8')
                return JsonResponse({
                    'status': 'success', 
                    'logs': logs,
                    'type': 'execv',
                    'warning': f'Error tailing file: {str(exec_error)}. Showing container logs instead.'
                })
        else:
            return JsonResponse({'status': 'error', 'message': 'Container not running'})
    except docker.errors.NotFound:
        return JsonResponse({'status': 'error', 'message': 'Container not found'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

def container_logs_follow(request, container_id):
    """Get real-time logs from a specific file inside a container using tail -f"""
    container = get_object_or_404(DeployedContainer, id=container_id)
    
    try:
        if container.container_id and container.status == 'running':
            docker_container = client.containers.get(container.container_id)
            
            # Hard-coded log file path: /xwangnet/execv.log
            log_file_path = "/xwangnet/execv.log"
            
            # Use exec to tail -f the specific file inside the container
            try:
                # Execute tail -f command inside the container using sh -c
                exec_result = docker_container.exec_run(
                    f'sh -c "tail -f -n 50 {log_file_path}"',
                    tty=False,
                    stream=True
                )
                
                # Stream the output
                def generate():
                    for line in exec_result.output:
                        yield f"data: {line.decode('utf-8')}\n\n"
                
                response = HttpResponse(generate(), content_type='text/event-stream')
                response['Cache-Control'] = 'no-cache'
                response['X-Accel-Buffering'] = 'no'
                return response
                    
            except Exception as exec_error:
                return JsonResponse({
                    'status': 'error', 
                    'message': f'Error following file: {str(exec_error)}'
                })
        else:
            return JsonResponse({'status': 'error', 'message': 'Container not running'})
    except docker.errors.NotFound:
        return JsonResponse({'status': 'error', 'message': 'Container not found'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

def container_buttons(request, container_id):
    container = get_object_or_404(DeployedContainer, id=container_id)
    status = request.GET.get('status', container.status)
    
    html = render_to_string('container_buttons.html', {
        'container': container,
        'status': status,
        'deployment': container.deployment
    })
    return HttpResponse(html)

def remove_container(request, container_id):
    if request.method == 'POST':
        try:
    
            container = client.containers.get(container_id)
            
            # Only allow removing stopped containers
            if container.status != 'running':
                container.remove()
                messages.success(request, f'Container {container.name} removed successfully')
            else:
                messages.error(request, 'Cannot remove running container')
                
        except docker.errors.NotFound:
            messages.error(request, 'Container not found')
        except Exception as e:
            messages.error(request, f'Error removing container: {str(e)}')
            
    return redirect('container_list')

def delete_deployed_container(request, container_id):
    if request.method == 'POST':
        container = get_object_or_404(DeployedContainer, id=container_id)
        deployment_id = container.deployment.id
        
        try:
            # If container is running in Docker, stop and remove it
            if container.container_id and container.status == 'running':
                try:
                    docker_container = client.containers.get(container.container_id)
                    docker_container.stop()
                    docker_container.remove()
                except docker.errors.NotFound:
                    pass  # Container already removed from Docker
                
            # Remove proxy if this is a webtop container
            if container.device.name == 'webtop' and container.hostname:
                ProxyManager.remove_webtop_proxy(container.hostname)
                
            # Delete the DeployedContainer record
            container.delete()
            messages.success(request, f'Container {container.hostname} removed from deployment')
            
        except Exception as e:
            messages.error(request, f'Error removing container: {str(e)}')
        
        return redirect('deployment_detail', deployment_id=deployment_id)
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

def add_deployed_container(request, deployment_id):
    if request.method == 'POST':
        deployment = get_object_or_404(Deployment, id=deployment_id)
        device_id = request.POST.get('device_id')
        
        if not device_id:
            messages.error(request, 'Device template is required')
            return redirect('deployment_detail', deployment_id=deployment_id)
            
        try:
            device = DeviceTemplate.objects.get(id=device_id)
            
            # Generate unique hostname
            base_hostname = f"{device.name}-{device.version}"
            existing_count = DeployedContainer.objects.filter(
                deployment=deployment,
                hostname__startswith=base_hostname
            ).count()
            hostname = f"{base_hostname}-{existing_count + 1}"
            
            # Create new container
            DeployedContainer.objects.create(
                deployment=deployment,
                device=device,
                hostname=hostname,
                status='stopped'
            )
            
            messages.success(request, f'Container {hostname} added to deployment')
            
        except DeviceTemplate.DoesNotExist:
            messages.error(request, 'Invalid device template')
        except Exception as e:
            messages.error(request, f'Error adding container: {str(e)}')
            
        return redirect('deployment_detail', deployment_id=deployment_id)
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

def networks(request):
    """View for managing Docker networks and their configurations"""
    
    # Get all networks from Docker and database
    docker_networks = client.networks.list()
    db_networks = NetworkConfiguration.objects.all()
    
    # Collect all subnets for clash detection
    network_subnets = []
    for network in docker_networks:
        # Safely get IPAM config with proper error handling
        ipam_config = network.attrs.get('IPAM', {}).get('Config', [])
        if ipam_config:  # Only process if IPAM config exists
            for config in ipam_config:
                subnet = config.get('Subnet')
                if subnet:
                    network_subnets.append({
                        'subnet': subnet,
                        'network_name': network.name,
                        'is_active': True
                    })
    
    # Add planned networks from database
    for network in db_networks:
        network_subnets.append({
            'subnet': network.subnet,
            'network_name': network.name,
            'is_active': False
        })
    
    # Detect subnet clashes
    subnet_clashes = []
    for i, net1 in enumerate(network_subnets):
        for net2 in network_subnets[i+1:]:
            if is_subnet_overlap(net1['subnet'], net2['subnet']):
                clash = {
                    'network1': net1['network_name'],
                    'network2': net2['network_name'],
                    'subnet1': net1['subnet'],
                    'subnet2': net2['subnet'],
                    'status': 'Active Conflict' if net1['is_active'] and net2['is_active'] else 'Planning Conflict'
                }
                subnet_clashes.append(clash)
    
    # Prepare network data for template with safer attribute access
    networks_data = []
    for network in docker_networks:
        # Get IPAM config safely with proper fallback
        ipam_configs = network.attrs.get('IPAM', {}).get('Config', [])
        ipam_config = ipam_configs[0] if ipam_configs else {}
        
        # Get detailed network info including containers
        network.reload()  # Refresh network data
        
        # Get container information from network attributes
        containers = []
        network_containers = network.attrs.get('Containers', {})
        
        for container_id, container_attrs in network_containers.items():
            container_info = {
                'id': container_id,
                'name': container_attrs.get('Name'),
                'mac_address': container_attrs.get('MacAddress'),
                'ipv4_address': container_attrs.get('IPv4Address', '').split('/')[0],  # Remove CIDR notation
            }
            containers.append(container_info)
        
        network_info = {
            'id': network.id,
            'name': network.name,
            'driver': network.attrs.get('Driver', 'unknown'),
            'subnet': ipam_config.get('Subnet', 'N/A'),
            'gateway': ipam_config.get('Gateway', 'N/A'),
            'status': 'active',
            'container_count': len(containers),
            'containers': containers,
            'created_at': network.attrs.get('Created'),
            'scope': network.attrs.get('Scope'),
            'internal': network.attrs.get('Internal', False),
            'enable_ipv6': network.attrs.get('EnableIPv6', False),
        }
        networks_data.append(network_info)

    # Add planned networks from database
    for network in db_networks:
        if not any(n['name'] == network.name for n in networks_data):
            network_info = {
                'id': f'planned_{network.id}',
                'name': network.name,
                'driver': 'bridge',
                'subnet': network.subnet,
                'gateway': network.gateway,
                'status': 'planned',
                'container_count': 0,
                'internal': network.isolated
            }
            networks_data.append(network_info)
    
    context = {
        'networks': networks_data,
        'subnet_clashes': subnet_clashes,
    }
    
    return render(request, 'networks.html', context)

def network_action(request, network_id):
    """Handle network actions (start/stop/delete)"""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)
  
    try:
        # Get action from POST data
        action = request.POST.get('action')
        
        if not action:
            return JsonResponse({
                'status': 'error',
                'message': 'No action specified'
            }, status=400)
        
        if network_id.startswith('planned_'):
            # Handle planned network actions
            db_network = get_object_or_404(NetworkConfiguration, id=network_id.replace('planned_', ''))
            
            if action == 'start':
                # Create the network in Docker with optional subnet/gateway
                ipam_config = {}
                subnet = db_network.subnet
                gateway = db_network.gateway
                
                if subnet or gateway:
                    ipam_pool = {}
                    if subnet:
                        ipam_pool['subnet'] = subnet
                    if gateway:
                        ipam_pool['gateway'] = gateway
                    ipam_config = docker.types.IPAMConfig(
                        pool_configs=[docker.types.IPAMPool(**ipam_pool)]
                    )
                
                network = client.networks.create(
                    name=db_network.name,
                    driver='bridge',
                    internal=db_network.isolated,
                    ipam=ipam_config if ipam_config else None
                )
                return JsonResponse({'status': 'success', 'message': f'Network {db_network.name} created'})
            
            elif action == 'delete':
                db_network.delete()
                return JsonResponse({'status': 'success', 'message': 'Network configuration deleted'})
            
            else:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Invalid action {action} for planned network'
                }, status=400)
        
        else:
            # Handle active network actions
            network = client.networks.get(network_id)
            
            if action == 'stop':
                network.remove()
                return JsonResponse({'status': 'success', 'message': f'Network {network.name} removed'})
            else:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Invalid action {action} for active network'
                }, status=400)
            
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

def is_subnet_overlap(subnet1, subnet2):
    """Helper function to check if two subnets overlap"""
    try:
        from ipaddress import ip_network
        net1 = ip_network(subnet1, strict=False)
        net2 = ip_network(subnet2, strict=False)
        return net1.overlaps(net2)
    except ValueError:
        return False

def upload_firmware(request):
    if request.method == 'POST':
        firmware_name = request.POST['firmware_name']
        firmware_description = request.POST['firmware_description']
        contact_name = request.POST['contact_name']
        contact_email = request.POST['contact_email']
        contact_phone = request.POST['contact_phone']
        firmware_file = request.FILES['firmware_file']

        # Validate email
        try:
            validate_email(contact_email)
        except ValidationError:
            return HttpResponse('Invalid email address', status=400)

        # Validate phone number
        phone_pattern = re.compile(r'^\+[1-9]\d{1,14}$')
        if not phone_pattern.match(contact_phone):
            return HttpResponse('Invalid phone number', status=400)

        # Save the file
        fs = FileSystemStorage(location='FirmwareUploads/')
        filename = fs.save(firmware_file.name, firmware_file)
        uploaded_file_url = fs.url(filename)

        return render(request, 'upload_firmware.html', {
            'uploaded_file_url': uploaded_file_url
        })
    return render(request, 'upload_firmware.html')

def deploy_suricata(request, deployment_id):
    """Deploy Suricata as a network gateway"""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    deployment = get_object_or_404(Deployment, id=deployment_id)
    
    try:
        # Ensure Suricata directories exist with proper permissions
        base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        suricata_dir = os.path.join(base_dir, 'suricata')
        
        for dir_name in ['logs']:
            dir_path = os.path.join(suricata_dir, f'{dir_name}-{deployment.id}')
            os.makedirs(dir_path, exist_ok=True)
            # Set permissions that work for both your user and the container
            os.chmod(dir_path, 0o777)  # Everyone can read/write

        # Step 1: Get network name and bridge interface
        network_name = deployment.network.name
        
        # Get Docker network and bridge interface for monitoring
        docker_network = client.networks.get(deployment.docker_network_id)
        bridge_id = docker_network.id[:12]
        bridge_interface = f"br-{bridge_id}"
        
        logger.info(f"Deploying Suricata to monitor bridge: {bridge_interface}")

        # Step 2: Deploy Suricata in host mode to monitor bridge
        suricata_container = client.containers.run(
            "jasonish/suricata:latest",
            name=f"suricata-{deployment.id}",
            network_mode="host",  # Host mode to access bridge interface
            cap_add=["NET_ADMIN", "NET_RAW", "SYS_NICE"],
            volumes={
                os.path.join(suricata_dir, f'logs-{deployment.id}'): {'bind': '/var/log/suricata', 'mode': 'rw'},
                os.path.join(suricata_dir, 'configs'): {'bind': '/etc/suricata', 'mode': 'rw'}
            },
            detach=True,
            command=f"-i {bridge_interface}"  # Monitor the Docker bridge
        )

        # Wait for Suricata to start
        time.sleep(3)
        
        # Get bridge IP as "Suricata IP" for display purposes
        try:
            result = subprocess.run(['ip', 'addr', 'show', bridge_interface], 
                                  capture_output=True, text=True)
            match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', result.stdout)
            suricata_ip = match.group(1) if match else "N/A (Bridge Tap Mode)"
        except Exception:
            suricata_ip = "N/A (Bridge Tap Mode)"

        logger.info(f"Suricata monitoring {bridge_interface}, bridge IP: {suricata_ip}")

        # No NAT/routing configuration needed in bridge tap mode
        # Suricata is passively monitoring all traffic on the bridge

        # Save Suricata Container ID
        deployment.suricata_container_id = suricata_container.id
        deployment.suricata_status = 'active'
        deployment.save()

        return JsonResponse({
            'status': 'success',
            'message': f'Suricata is now the gateway for {deployment.name}'
        })

    except Exception as e:
        # Cleanup if something goes wrong
        try:
            if 'suricata_container' in locals():
                suricata_container.remove(force=True)
        except:
            pass
            
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

def stop_suricata(request, deployment_id):
    """Stop and remove Suricata container for a deployment"""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    deployment = get_object_or_404(Deployment, id=deployment_id)
    
    try:
        if deployment.suricata_container_id:
            # Reset routing for all containers in the network first
            network = client.networks.get(deployment.network.name)
            for container in network.containers:
                if container.id != deployment.suricata_container_id:
                    logger.info(f"Resetting routing for container: {container.name}")
                    container.reload()  # Refresh container info
                    configure_container_routing(container, restore_default=True)

            # Remove Suricata container
            try:
                container = client.containers.get(deployment.suricata_container_id)
                container.stop()
                container.remove()
            except docker.errors.NotFound:
                pass

            # Update deployment
            deployment.suricata_container_id = None
            deployment.suricata_status = 'inactive'
            deployment.save()

        return JsonResponse({
            'status': 'success',
            'message': 'Suricata monitoring deactivated and container routing restored'
        })

    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

def get_suricata_logs(request, deployment_id):
    """Get filtered and formatted Suricata logs showing only internal network traffic"""
    deployment = get_object_or_404(Deployment, id=deployment_id)
    
    try:
        if not (deployment.suricata_container_id and deployment.suricata_status == 'active'):
            return JsonResponse({'status': 'error', 'logs': 'Suricata not running'})
        
        # Read eve.json log file
        base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        eve_log = os.path.join(base_dir, 'suricata', f'logs-{deployment.id}', 'eve.json')
        
        if not os.path.exists(eve_log):
            return JsonResponse({'status': 'success', 'logs': 'No logs available yet'})
        
        # Get network subnet for filtering
        import ipaddress as ip_module
        
        # Check if subnet is configured
        if not deployment.network.subnet or deployment.network.subnet.strip() == '':
            return JsonResponse({'status': 'success', 'logs': 'Network subnet not configured. Please configure subnet in network settings.'})
        
        try:
            network_subnet = ip_module.ip_network(deployment.network.subnet, strict=False)
        except Exception as e:
            return JsonResponse({'status': 'success', 'logs': f'Invalid network subnet configuration: {deployment.network.subnet}'})
        
        formatted_logs = []
        
        # Read last 100 lines of eve.json
        with open(eve_log, 'r') as f:
            lines = f.readlines()
            recent_lines = lines[-100:] if len(lines) > 100 else lines
        
        for line in recent_lines:
            try:
                data = json.loads(line.strip())
                
                # Filter: only flow events
                if data.get('event_type') != 'flow':
                    continue
                
                src_ip = data.get('src_ip', '')
                dest_ip = data.get('dest_ip', '')
                
                # Filter: at least one IP must be in internal network
                try:
                    src_in_network = ip_module.ip_address(src_ip) in network_subnet
                    dest_in_network = ip_module.ip_address(dest_ip) in network_subnet
                except:
                    continue  # Skip invalid IPs
                
                if not (src_in_network or dest_in_network):
                    continue  # Skip traffic not involving internal network
                
                # Format the log entry
                timestamp = data.get('timestamp', '')[:19]  # Just date and time
                proto = data.get('proto', 'N/A')
                src_port = data.get('src_port', '')
                dest_port = data.get('dest_port', '')
                flow = data.get('flow', {})
                pkts_to = flow.get('pkts_toserver', 0)
                pkts_from = flow.get('pkts_toclient', 0)
                bytes_to = flow.get('bytes_toserver', 0)
                bytes_from = flow.get('bytes_toclient', 0)
                
                # Direction indicator
                if src_in_network and dest_in_network:
                    direction = "↔"  # Internal to internal
                elif src_in_network:
                    direction = "→"  # Outbound
                else:
                    direction = "←"  # Inbound
                
                # Build log line
                port_info = f":{src_port}→:{dest_port}" if src_port and dest_port else ""
                log_line = f"{timestamp} | {src_ip}{port_info if src_port else ''} {direction} {dest_ip}{port_info if dest_port else ''} | {proto} | ↑{pkts_to}↓{pkts_from} pkts | ↑{bytes_to}↓{bytes_from} bytes"
                formatted_logs.append(log_line)
                
            except json.JSONDecodeError:
                continue
            except Exception:
                continue
        
        # Return most recent first
        formatted_logs.reverse()
        logs_text = '\n'.join(formatted_logs[-50:]) if formatted_logs else 'No internal network traffic detected yet'
        
        return JsonResponse({'status': 'success', 'logs': logs_text})
        
    except Exception as e:
        return JsonResponse({'status': 'error', 'logs': f'Error reading logs: {str(e)}'})

def container_shells(request, container_id):
    container = get_object_or_404(DeployedContainer, id=container_id)
    return render(request, 'container_shells.html', {
        'container': container,
        'container_id': container.container_id
    })

def get_monitoring_status(request, deployment_id):
    """Get monitoring status and statistics"""
    deployment = get_object_or_404(Deployment, id=deployment_id)
    
    try:
        if not deployment.suricata_container_id:
            return JsonResponse({
                'status': 'inactive',
                'message': 'Monitoring not active'
            })
            
        container = client.containers.get(deployment.suricata_container_id)
        
        # Get Suricata stats from eve.json
        base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        eve_log = os.path.join(base_dir, 'suricata', f'logs-{deployment.id}', f'eve-{deployment.id}.json')
        
        stats = {
            'container_status': container.status,
            'alerts': 0,
            'packets': {
                'processed': 0,
                'accepted': 0,
                'dropped': 0,
                'failed': 0
            },
            'bytes': {
                'processed': 0
            },
            'flows': {
                'tcp': 0,
                'udp': 0,
                'other': 0
            },
            'uptime': 0,
            'last_update': None
        }
        
        if os.path.exists(eve_log):
            with open(eve_log, 'r') as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        if event.get('event_type') == 'stats':
                            # Update packet stats
                            packets = event.get('stats', {}).get('capture', {}).get('kernel_packets', {})
                            stats['packets'].update({
                                'processed': packets.get('received', 0),
                                'dropped': packets.get('dropped', 0),
                                'failed': packets.get('failures', 0)
                            })
                            
                            # Update flow stats
                            flows = event.get('stats', {}).get('flows', {})
                            stats['flows'].update({
                                'tcp': flows.get('tcp', 0),
                                'udp': flows.get('udp', 0),
                                'other': flows.get('other', 0)
                            })
                            
                            # Update other stats
                            stats['uptime'] = event.get('stats', {}).get('uptime', 0)
                            stats['last_update'] = event.get('timestamp', None)
                            
                        elif event.get('event_type') == 'alert':
                            stats['alerts'] += 1
                            
                    except:
                        continue
        
        # Get live container logs for additional info
        logs = container.logs(tail=10).decode('utf-8')
        stats['recent_logs'] = logs.split('\n')
        
        return JsonResponse({
            'status': 'success',
            'data': stats
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

def configure_container_routing(container, suricata_ip=None, remove_only=False, restore_default=False):
    """
    Configure or remove routing for a container
    Args:
        container: Docker container object
        suricata_ip: IP address of Suricata container (None if removing routes)
        remove_only: If True, only remove existing routes without adding new ones
        restore_default: If True, restore default gateway from network config
    """
    try:
        # Get network gateway if we need to restore default
        if restore_default:
            network_settings = container.attrs.get('NetworkSettings', {}).get('Networks', {})
            if network_settings:
                network_name = list(network_settings.keys())[0]  # Get first network
                gateway = network_settings[network_name].get('Gateway')
                if gateway:
                    suricata_ip = gateway
                    remove_only = False

        # First, check if the container has ip command
        ip_check = container.exec_run("which ip")
        if ip_check.exit_code == 0:
            # Container has ip command
            container.exec_run("ip route del default", privileged=True)
            if not remove_only and suricata_ip:
                container.exec_run(f"ip route add default via {suricata_ip} dev eth0", privileged=True)
                # Verify route
                result = container.exec_run("ip route show default", privileged=True)
                logger.debug(f"Route verification for {container.name}: {result.output.decode()}")
            return True
        
        # Try route command instead
        route_check = container.exec_run("which route")
        if route_check.exit_code == 0:
            container.exec_run("route del default", privileged=True)
            if not remove_only and suricata_ip:
                container.exec_run(f"route add default gw {suricata_ip}", privileged=True)
                # Verify route
                result = container.exec_run("route -n", privileged=True)
                logger.debug(f"Route verification for {container.name}: {result.output.decode()}")
            return True
        
        if not remove_only and suricata_ip:
            # If neither command exists, try to install ip command
            logger.info(f"Installing iproute2 in container {container.name}")
            try:
                container.exec_run("apt-get update", privileged=True)
                container.exec_run("apt-get install -y iproute2", privileged=True)
            except:
                try:
                    container.exec_run("apk add --no-cache iproute2", privileged=True)
                except:
                    container.exec_run("yum install -y iproute", privileged=True)
            
            # Try again with ip command
            container.exec_run("ip route del default", privileged=True)
            container.exec_run(f"ip route add default via {suricata_ip} dev eth0", privileged=True)
            # Verify route
            result = container.exec_run("ip route show default", privileged=True)
            logger.debug(f"Route verification for {container.name}: {result.output.decode()}")
            return True
            
        return False
        
    except Exception as e:
        logger.warning(f"Failed to configure routing for container {container.name}: {str(e)}")
        return False

def cleanup_webtop_network(deployment_id):
    """Remove the deployment-specific webtop network if it exists"""
    try:
        webtop_network = client.networks.get(f'webtop-network-{deployment_id}')
        webtop_network.remove()
    except docker.errors.NotFound:
        pass  # Network doesn't exist or already removed
    except docker.errors.APIError as e:
        logger.warning(f"Failed to remove webtop network: {str(e)}")

# External IP Configuration API Endpoints

def list_interfaces_api(request):
    """API endpoint to list available network interfaces"""
    from xwangnet.services.interface_manager import InterfaceManager
    
    try:
        interfaces = InterfaceManager.list_available_interfaces()
        
        # Mark server IPs as unavailable to prevent accidental selection
        server_ips = set()
        for iface in interfaces:
            # Mark all current server IPs
            server_ips.add(iface['ip'])
        
        # Add flag to indicate which IPs are server IPs (should not be used)
        for iface in interfaces:
            iface['is_server_ip'] = True  # All existing IPs are server IPs
            iface['warning'] = '⚠️ Server IP - Will disconnect SSH/webserver if selected!'
        
        return JsonResponse({
            'success': True,
            'interfaces': interfaces,
            'server_ips': list(server_ips)
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e),
            'interfaces': [],
            'server_ips': []
        }, status=500)

def validate_interface_api(request):
    """API endpoint to validate interface configuration before deployment"""
    from xwangnet.services.interface_manager import InterfaceManager
    from xwangnet.services.nat_manager import NATManager
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST method required'}, status=405)
    
    try:
        data = json.loads(request.body)
        create_new = data.get('create_new', False)
        use_dhcp = data.get('use_dhcp', True)
        external_ip = data.get('external_ip', '')
        external_interface_json = data.get('external_interface', '')
        
        # Test 1: Check iptables accessibility
        nat_test = NATManager.test_nat_configuration()
        if not nat_test['success']:
            return JsonResponse({
                'success': False,
                'message': f"iptables test failed: {nat_test['message']}"
            })
        
        # Test 2: Validate configuration based on mode
        if create_new:
            # Test interface creation capability
            iface_test = InterfaceManager.test_interface_creation()
            if not iface_test['success']:
                return JsonResponse({
                    'success': False,
                    'message': f"Interface creation test failed: {iface_test['message']}"
                })
            
            if not use_dhcp and not external_ip:
                return JsonResponse({
                    'success': False,
                    'message': 'Manual IP address is required when DHCP is not used'
                })
            
            # Validate IP format if manual
            if not use_dhcp:
                import ipaddress
                try:
                    ipaddress.ip_address(external_ip)
                except ValueError:
                    return JsonResponse({
                        'success': False,
                        'message': f'Invalid IP address format: {external_ip}'
                    })
            
            return JsonResponse({
                'success': True,
                'message': 'Configuration validated successfully. Ready for deployment.'
            })
        else:
            # Validate existing interface selection
            if not external_interface_json:
                return JsonResponse({
                    'success': False,
                    'message': 'Please select an interface'
                })
            
            try:
                iface_data = json.loads(external_interface_json)
                interface_name = iface_data.get('interface')
                interface_ip = iface_data.get('ip')
                
                # Verify interface still exists and has IP
                verify_result = InterfaceManager.verify_interface_ip(interface_name)
                if not verify_result['success']:
                    return JsonResponse({
                        'success': False,
                        'message': f"Interface validation failed: {verify_result['message']}"
                    })
                
                # Check for port conflicts
                conflict_check = NATManager.check_port_conflicts(interface_ip)
                warning_msg = ''
                if conflict_check['has_conflicts']:
                    warning_msg = f" Warning: {conflict_check['message']}"
                
                return JsonResponse({
                    'success': True,
                    'message': f'Interface {interface_name} ({interface_ip}) is ready.{warning_msg}'
                })
                
            except json.JSONDecodeError:
                return JsonResponse({
                    'success': False,
                    'message': 'Invalid interface data format'
                })
    
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Validation error: {str(e)}'
        }, status=500)


def deployment_status_api(request, deployment_id):
    """API endpoint to get current deployment status including network IP and container IPs"""
    deployment = get_object_or_404(Deployment, id=deployment_id)
    
    try:
        # Get network information
        network_data = {
            'name': deployment.network.name,
            'external_ip': deployment.network.external_ip if deployment.network.use_external_ip else None,
            'external_interface': deployment.network.external_interface if deployment.network.use_external_ip else None,
            'subnet': deployment.network.subnet,
        }
        
        # Get container information with internal IPs
        containers_data = []
        for container in deployment.containers.all():
            container_info = {
                'id': container.id,
                'hostname': container.hostname,
                'status': container.status,
                'internal_ip': container.internal_ip,
                'device_name': container.device.name,
            }
            containers_data.append(container_info)
        
        return JsonResponse({
            'status': 'success',
            'network': network_data,
            'containers': containers_data,
            'network_status': deployment.network_status,
            'suricata_status': deployment.suricata_status,
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

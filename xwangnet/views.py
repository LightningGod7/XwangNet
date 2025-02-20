from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import DockerNetwork, DockerContainer, DeviceTemplate, NetworkConfiguration, DeviceInstance, Deployment, DeployedContainer
from .forms import DockerNetworkForm, DockerContainerForm, NetworkConfigurationForm, DeviceInstanceForm, ComposeGeneratorForm
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

client = docker.from_env()

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
                    selected_devices.append({
                        'id': device.id,
                        'name': f"{device.name}-{device.version}-{i+1}",
                        'original_name': device.name
                    })
            
            request.session['selected_devices'] = selected_devices
            return redirect('network_config')
    else:
        form = ComposeGeneratorForm()
    
    return render(request, 'device_selection.html', {'form': form})

def network_config(request):
    if request.method == 'POST':
        form = NetworkConfigurationForm(request.POST)
        if form.is_valid():
            network = form.save()
            request.session['network_id'] = network.id
            return redirect('compose_preview')
    else:
        form = NetworkConfigurationForm()
    return render(request, 'network_config.html', {'form': form})

def compose_preview(request):
    network = NetworkConfiguration.objects.get(id=request.session['network_id'])
    selected_devices = request.session['selected_devices']
    
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
        'network': network
    })

def compose_generator(request):
    return render(request, 'compose_generator.html')

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
                DeployedContainer.objects.create(
                    deployment=deployment,
                    device=device,
                    hostname=device_info['name'],
                    status='stopped'
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
        deployment.delete()
        return JsonResponse({'status': 'success', 'message': 'Deployment deleted successfully'})
    
    return render(request, 'deployment_detail.html', {
        'deployment': deployment,
        'deployments': Deployment.objects.all().order_by('-created_at'),
        'device_templates': DeviceTemplate.objects.all()
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
                
            except docker.errors.APIError as e:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Docker API Error: {str(e)}'
                }, status=500)
                
        elif action == 'down' and deployment.network_status == 'up':
            try:
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
            
            # Create container with image ID
            docker_container = client.containers.run(
                image_id,
                name=f"{container.hostname}-{container.id}",
                hostname=container.hostname,
                network=container.deployment.network.name,
                environment=container.device.environment,
                ports=container.device.ports,
                detach=True,
                remove=True
            )
            docker_container.reload()
            container.container_id = docker_container.id
            container.status = 'running'
            container.internal_ip = docker_container.attrs['NetworkSettings']['Networks'].get(container.deployment.network.name)['IPAddress']
            container.save()

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
                network_info = network_settings.get(container.deployment.network.name)
                
                if network_info and network_info.get('IPAddress'):  # Make sure we have an IP
                    hostname = ProxyManager.generate_webtop_hostname()
                    container_ip = network_info['IPAddress']
                    
                    print(f"Container IP: {container_ip}")  # Debug print
                    
                    success = ProxyManager.add_webtop_proxy(
                        hostname,
                        container_ip,  # This should be a valid IP address
                        3000  # Webtop default port
                    )
                    
                    if success:
                        print(f"Successfully added proxy for {hostname} -> {container_ip}:3000")
                        container.hostname = hostname
                        container.save()
                        
                else:
                    print(f"Network info: {network_info}")  # Debug print
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Could not get container IP address'
                    }, status=500)
            
        elif action == 'stop':
            if container.container_id:
                docker_container = client.containers.get(container.container_id)
                docker_container.stop()
                container.status = 'stopped'
                
                # Remove proxy if this is a webtop container
                if container.device.name == 'webtop' and container.hostname:
                    ProxyManager.remove_webtop_proxy(container.hostname)
                
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
                    
                    # Add new proxy
                    container_info = docker_container.attrs
                    network_settings = container_info['NetworkSettings']['Networks']
                    network_info = network_settings.get(container.deployment.network.name)
                    
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
    container = get_object_or_404(DeployedContainer, id=container_id)
    
    try:
        if container.container_id and container.status == 'running':
            docker_container = client.containers.get(container.container_id)
            logs = docker_container.logs(tail=100).decode('utf-8')
            return JsonResponse({'status': 'success', 'logs': logs})
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
        
        # Get container information
        containers = []
        for container_id, container_attrs in network.attrs.get('Containers', {}).items():
            containers.append({
                'id': container_id,
                'name': container_attrs.get('Name'),
                'mac_address': container_attrs.get('MacAddress'),
                'ipv4_address': container_attrs.get('IPv4Address'),
            })
        
        network_info = {
            'id': network.id,
            'name': network.name,
            'driver': network.attrs.get('Driver', 'unknown'),
            'subnet': ipam_config.get('Subnet', 'N/A'),
            'gateway': ipam_config.get('Gateway', 'N/A'),
            'status': 'active',
            'container_count': len(network.attrs.get('Containers', {})),
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
    """Deploy Suricata container to monitor a specific deployment network"""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    deployment = get_object_or_404(Deployment, id=deployment_id)
    
    try:
        # Get the deployment network interface name
        deployment_network = client.networks.get(deployment.docker_network_id)
        network_interface = f"{deployment.docker_network_id[:12]}"  # Docker bridge interface name
        
        # Create absolute paths for Suricata volumes
        base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
        suricata_dir = os.path.join(base_dir, 'suricata')
        
        # Ensure directories exist
        for dir_name in ['logs']:
            dir_path = os.path.join(suricata_dir, f'{dir_name}-{deployment.id}')
            os.makedirs(dir_path, exist_ok=True)

        # Deploy Suricata container with host networking to monitor bridge interface
        suricata_container = client.containers.run(
            "jasonish/suricata:latest",
            name=f"suricata-{deployment.id}",
            cap_add=["NET_ADMIN", "NET_RAW", "SYS_NICE"],
            volumes={
                os.path.join(suricata_dir, f'logs-{deployment.id}'): {'bind': '/var/log/suricata', 'mode': 'rw'},
                os.path.join(suricata_dir, 'configs'): {'bind': '/etc/suricata', 'mode': 'rw'}  # Add config volume
            },
            restart_policy={"Name": "unless-stopped"},
            network_mode="host",  # Use host networking to access bridge interface
            detach=True,
            command=f"-i br-{network_interface}"  # Add the interface
        )

        # Update deployment with Suricata info
        deployment.suricata_container_id = suricata_container.id
        deployment.suricata_status = 'active'
        deployment.save()

        return JsonResponse({
            'status': 'success',
            'message': f'Suricata monitoring activated for deployment {deployment.name}'
        })

    except Exception as e:
        # Clean up if something goes wrong
        try:
            if 'suricata_container' in locals():
                suricata_container.remove(force=True)
        except:
            pass  # Ignore cleanup errors
            
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
            # Remove Suricata container
            container = client.containers.get(deployment.suricata_container_id)
            container.stop()
            container.remove()

            # Update deployment
            deployment.suricata_container_id = None
            deployment.suricata_status = 'inactive'
            deployment.save()

        return JsonResponse({
            'status': 'success',
            'message': 'Suricata monitoring deactivated'
        })

    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

def get_suricata_logs(request, deployment_id):
    """Get Suricata logs for a deployment"""
    deployment = get_object_or_404(Deployment, id=deployment_id)
    
    try:
        if deployment.suricata_container_id and deployment.suricata_status == 'active':
            container = client.containers.get(deployment.suricata_container_id)
            logs = container.logs(tail=100).decode('utf-8')
            return JsonResponse({'status': 'success', 'logs': logs})
        else:
            return JsonResponse({'status': 'error', 'message': 'Suricata not running'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

def container_shells(request, container_id):
    container = get_object_or_404(DeployedContainer, id=container_id)
    return render(request, 'container_shells.html', {
        'container': container,
        'container_id': container.container_id
    })


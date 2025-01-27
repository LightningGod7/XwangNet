from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import DockerNetwork, DockerContainer, DeviceTemplate, NetworkConfiguration, DeviceInstance, Deployment, DeployedContainer
from .forms import DockerNetworkForm, DockerContainerForm, NetworkConfigurationForm, DeviceInstanceForm, ComposeGeneratorForm
import docker
import yaml
from django.http import JsonResponse, HttpResponse
import json
from django.template.loader import render_to_string
from django.core.paginator import Paginator

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

def container_list_api(request):
    client = docker.from_env()
    deployments = Deployment.objects.all().prefetch_related('containers')
    result = {}
    
    try:
        docker_containers = client.containers.list(all=True)
        container_map = {c.id: c for c in docker_containers}
        
        for deployment in deployments:
            containers_data = []
            for deployed_container in deployment.containers.all():
                try:
                    if deployed_container.container_id in container_map:
                        container = container_map[deployed_container.container_id]
                        
                        # Initialize stats
                        stats = {
                            'cpu_usage': 0,
                            'mem_usage': 0,
                            'mem_limit': 0,
                            'mem_percent': 0
                        }
                        
                        if container.status == 'running':
                            try:
                                container_stats = container.stats(stream=False)
                                
                                # Calculate CPU usage with fallback options
                                try:
                                    cpu_delta = container_stats['cpu_stats']['cpu_usage']['total_usage'] - \
                                              container_stats['precpu_stats']['cpu_usage']['total_usage']
                                    system_delta = container_stats['cpu_stats']['system_cpu_usage'] - \
                                                 container_stats['precpu_stats']['system_cpu_usage']
                                    
                                    # Get number of CPUs
                                    if 'online_cpus' in container_stats['cpu_stats']:
                                        num_cpus = container_stats['cpu_stats']['online_cpus']
                                    elif 'percpu_usage' in container_stats['cpu_stats']['cpu_usage']:
                                        num_cpus = len(container_stats['cpu_stats']['cpu_usage']['percpu_usage'])
                                    else:
                                        num_cpus = 1
                                    
                                    if system_delta > 0:
                                        cpu_usage = (cpu_delta / system_delta) * 100 * num_cpus
                                    else:
                                        cpu_usage = 0
                                        
                                except (KeyError, TypeError, ZeroDivisionError) as e:
                                    print(f"CPU calculation fallback for {deployed_container.hostname}: {str(e)}")
                                    # Fallback to simpler CPU calculation
                                    try:
                                        cpu_usage = (container_stats['cpu_stats']['cpu_usage']['total_usage'] / 
                                                   container_stats['cpu_stats']['system_cpu_usage']) * 100
                                    except:
                                        cpu_usage = 0
                                
                                # Calculate memory usage with error handling
                                try:
                                    mem_usage = container_stats['memory_stats'].get('usage', 0)
                                    mem_limit = container_stats['memory_stats'].get('limit', 0)
                                    mem_percent = (mem_usage / mem_limit) * 100 if mem_limit > 0 else 0
                                except (KeyError, ZeroDivisionError):
                                    mem_usage = 0
                                    mem_limit = 0
                                    mem_percent = 0
                                
                                stats = {
                                    'cpu_usage': round(max(0, min(cpu_usage, 100)), 2),  # Clamp between 0-100
                                    'mem_usage': round(mem_usage / (1024 * 1024), 2),
                                    'mem_limit': round(mem_limit / (1024 * 1024), 2),
                                    'mem_percent': round(mem_percent, 2)
                                }
                                
                            except Exception as e:
                                print(f"Error getting stats for container {deployed_container.hostname}: {str(e)}")

                        health_log = container.attrs.get('State', {}).get('Health', {}).get('Log', [])
                        health_log = health_log[-3:] if health_log else 'No health logs'
                        
                        # Get container info
                        container_info = {
                            'id': container.short_id,
                            'name': deployed_container.hostname,
                            'status': container.status,
                            'image': container.image.tags[0] if container.image.tags else container.image.short_id,
                            'created': container.attrs['Created'],
                            'ports': container.ports,
                            'network': deployment.network.name if deployment.network else None,
                            'health_status': container.attrs.get('State', {}).get('Health', {}).get('Status', 'No health check'),
                            'health_log': health_log,
                            'device_name': deployed_container.device.name,
                            **stats
                        }
                        containers_data.append(container_info)
                        
                except Exception as e:
                    print(f"Error processing container {deployed_container.hostname}: {str(e)}")
                    continue
            
            if containers_data:
                result[deployment.id] = {
                    'name': deployment.name,
                    'network_status': deployment.network_status,
                    'containers': containers_data
                }
        
        running_count = sum(1 for containers in result.values() 
                          for c in containers['containers'] if c['status'] == 'running')
        total_count = sum(len(containers['containers']) for containers in result.values())
        
        return JsonResponse({
            'status': 'success',
            'data': {
                'deployments': result,
                'running_count': running_count,
                'total_count': total_count
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

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
                        'name': f"{device.name}-{i+1}",
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
                'driver': network.network_type,
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
        'yaml_content': yaml_content,
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
    deployments = Deployment.objects.all().order_by('-created_at').select_related('network')  # Adjust 'related_field' as needed
    paginator = Paginator(deployments, 1)  # Show 1 deployments per page

    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'deployment_list.html', {
        'page_obj': page_obj
    })

def deployment_detail(request, deployment_id):
    deployment = get_object_or_404(Deployment, id=deployment_id)
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
                    base_hostname = f"{device.name}-{deployment.name}"
                    existing_count = DeployedContainer.objects.filter(
                        deployment=deployment,
                        hostname__startswith=base_hostname
                    ).count()
                    hostname = f"{base_hostname}-{existing_count + i + 1}"
                    
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
    client = docker.from_env()
    
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
                
                # Try to create the network
                network = client.networks.create(
                    deployment.network.name,
                    driver=deployment.network.network_type,
                    ipam=docker.types.IPAMConfig(
                        pool_configs=[
                            docker.types.IPAMPool(
                                subnet=deployment.network.subnet,
                                gateway=deployment.network.gateway
                            )
                        ]
                    )
                )
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
            container.container_id = docker_container.id
            container.status = 'running'
            container.save()
            
        elif action in ['stop', 'restart'] and container.container_id:
            docker_container = client.containers.get(container.container_id)
            if action == 'stop':
                docker_container.stop()
                container.status = 'stopped'
            else:
                docker_container.restart()
            container.save()
            
        return JsonResponse({
            'status': 'success', 
            'container_status': container.status,
            'container_id': container.container_id
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
    client = docker.from_env()
    
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
            client = docker.from_env()
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
            base_hostname = f"{device.name}-{deployment.name}"
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

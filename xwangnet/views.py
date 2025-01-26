from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import DockerNetwork, DockerContainer, DeviceTemplate, NetworkConfiguration, DeviceInstance, Deployment, DeployedContainer
from .forms import DockerNetworkForm, DockerContainerForm, NetworkConfigurationForm, DeviceInstanceForm, ComposeGeneratorForm
import docker
import yaml
from django.http import JsonResponse, HttpResponse
import json
from django.template.loader import render_to_string

client = docker.from_env()

def create_network(request):
    if request.method == 'POST':
        form = DockerNetworkForm(request.POST)
        if form.is_valid():
            network = form.save()
            client.networks.create(network.name, driver="bridge", ipam={
                'Config': [
                    {'Subnet': network.subnet, 'Gateway': network.gateway}
                ]
            })
            return redirect('network_list')
    else:
        form = DockerNetworkForm()
    return render(request, 'create_network.html', {'form': form})

def create_container(request):
    if request.method == 'POST':
        form = DockerContainerForm(request.POST)
        if form.is_valid():
            container = form.save()
            client.containers.run(
                container.image,
                name=container.name,
                network=container.network.name,
                command=container.command,
                environment=container.environment_vars.splitlines() if container.environment_vars else None,
                detach=True,
                remove=True
            )
            return redirect('container_list')
    else:
        form = DockerContainerForm()
    return render(request, 'create_container.html', {'form': form})

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
    #containers = DockerContainer.objects.all()
    containers = client.images.list()
    print(containers)  # Add this line to debug
    return render(request, 'container_list.html', {'containers': containers})

def device_selection(request):
    if request.method == 'POST':
        form = ComposeGeneratorForm(request.POST)
        if form.is_valid():
            selected_devices = form.cleaned_data['devices']
            request.session['selected_devices'] = [device.id for device in selected_devices]
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
    devices = DeviceTemplate.objects.filter(id__in=request.session['selected_devices'])
    
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
    
    for device in devices:
        compose_data['services'][device.name] = {
            'image': device.image,
            'networks': [network.name],
            'hostname': f"{device.name}-{network.name}"
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
            devices = DeviceTemplate.objects.filter(id__in=request.session['selected_devices'])
            
            # Create deployment record
            deployment = Deployment.objects.create(
                name=name,
                description=description,
                network=network,
                network_status='down'
            )
            
            # Create container records
            for device in devices:
                DeployedContainer.objects.create(
                    deployment=deployment,
                    device=device,
                    hostname=f"{device.name}-{network.name}",
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
    return render(request, 'deployment_detail.html', {
        'deployment': deployment,
        'deployments': Deployment.objects.all().order_by('-created_at')
    })

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
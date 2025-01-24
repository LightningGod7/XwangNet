from django.shortcuts import render, redirect
from .models import DockerNetwork, DockerContainer
from .forms import DockerNetworkForm, DockerContainerForm
import docker

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
                detach=True
            )
            return redirect('container_list')
    else:
        form = DockerContainerForm()
    return render(request, 'create_container.html', {'form': form})

def network_list(request):
    networks = DockerNetwork.objects.all()
    return render(request, 'network_list.html', {'networks': networks})

def container_list(request):
    containers = DockerContainer.objects.all()
    return render(request, 'container_list.html', {'containers': containers})

def compose_generator(request):
    return render(request, 'compose_generator.html')
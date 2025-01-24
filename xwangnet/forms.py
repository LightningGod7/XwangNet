from django import forms
from .models import DockerNetwork, DockerContainer

class DockerNetworkForm(forms.ModelForm):
    class Meta:
        model = DockerNetwork
        fields = ['name', 'subnet', 'gateway']

class DockerContainerForm(forms.ModelForm):
    class Meta:
        model = DockerContainer
        fields = ['name', 'image', 'network', 'command', 'environment_vars']
from django import forms
from .models import DockerNetwork, DockerContainer, DeviceTemplate, NetworkConfiguration, DeviceInstance

class DockerNetworkForm(forms.ModelForm):
    class Meta:
        model = DockerNetwork
        fields = ['name', 'subnet', 'gateway']

class DockerContainerForm(forms.ModelForm):
    class Meta:
        model = DockerContainer
        fields = ['name', 'image', 'network', 'command', 'environment_vars']

class NetworkConfigurationForm(forms.ModelForm):
    class Meta:
        model = NetworkConfiguration
        fields = ['name', 'network_type', 'subnet', 'gateway']

class DeviceInstanceForm(forms.ModelForm):
    class Meta:
        model = DeviceInstance
        fields = ['template', 'hostname', 'exposed_ports', 'environment_vars']

class ComposeGeneratorForm(forms.Form):
    devices = forms.ModelMultipleChoiceField(
        queryset=DeviceTemplate.objects.all(),
        widget=forms.CheckboxSelectMultiple
    )
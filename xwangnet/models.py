from django.db import models

class DockerNetwork(models.Model):
    name = models.CharField(max_length=255, unique=True)
    subnet = models.CharField(max_length=255)
    gateway = models.CharField(max_length=255)

    def __str__(self):
        return self.name

class DockerContainer(models.Model):
    name = models.CharField(max_length=255, unique=True)
    image = models.CharField(max_length=255)
    network = models.ForeignKey(DockerNetwork, on_delete=models.CASCADE, related_name='containers')
    command = models.CharField(max_length=255, blank=True, null=True)
    environment_vars = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

class DeviceTemplate(models.Model):
    name = models.CharField(max_length=255)
    image = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    version = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    docker_id = models.CharField(max_length=255, blank=True, null=True)
    docker_tags = models.JSONField(default=list)
    build_instructions = models.TextField(blank=True, help_text="Instructions for building this device image")
    ports = models.JSONField(default=dict, help_text="Default ports to expose")
    environment = models.JSONField(default=dict, help_text="Default environment variables")
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['name', 'version']

    def __str__(self):
        return f"{self.name} ({self.version})"

    def sync_with_docker(self):
        import docker
        client = docker.from_env()
        try:
            image = client.images.get(self.image)
            self.docker_id = image.id
            self.docker_tags = image.tags
            self.save()
            return True
        except docker.errors.ImageNotFound:
            return False

class NetworkConfiguration(models.Model):
    name = models.CharField(max_length=255, unique=True)
    NETWORK_TYPES = [
        ('bridge', 'Bridge'),
        ('host', 'Host-only'),
    ]
    
    network_type = models.CharField(max_length=10, choices=NETWORK_TYPES)
    subnet = models.CharField(max_length=255, blank=True)
    gateway = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=False)

class DeviceInstance(models.Model):
    template = models.ForeignKey(DeviceTemplate, on_delete=models.CASCADE)
    network = models.ForeignKey(NetworkConfiguration, on_delete=models.CASCADE)
    hostname = models.CharField(max_length=255)
    exposed_ports = models.JSONField(default=dict)
    environment_vars = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=50, default='stopped')

class Deployment(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    network = models.ForeignKey(NetworkConfiguration, on_delete=models.CASCADE)
    network_status = models.CharField(max_length=10, default='down')
    docker_network_id = models.CharField(max_length=64, null=True, blank=True)
    snort_container_id = models.CharField(max_length=64, null=True, blank=True)
    snort_status = models.CharField(max_length=10, default='inactive')

    def __str__(self):
        return self.name

class DeployedContainer(models.Model):
    deployment = models.ForeignKey(Deployment, on_delete=models.CASCADE, related_name='containers')
    device = models.ForeignKey(DeviceTemplate, on_delete=models.CASCADE)
    container_id = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=50, default='stopped')  # running/stopped/error
    hostname = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.deployment.name} - {self.device.name}"
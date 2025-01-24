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
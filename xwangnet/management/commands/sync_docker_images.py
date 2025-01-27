from django.core.management.base import BaseCommand
from xwangnet.models import DeviceTemplate
import docker

class Command(BaseCommand):
    help = 'Syncs Docker images with device templates'

    def handle(self, *args, **kwargs):
        client = docker.from_env()
        images = client.images.list()
        
        for image in images:
            for tag in image.tags:
                name = tag.split(':')[0].split('/')[-1]
                version = tag.split(':')[1] if ':' in tag else 'latest'
                
                device, created = DeviceTemplate.objects.get_or_create(
                    name=name,
                    version=version,
                    defaults={
                        'image': tag,
                        'docker_id': image.id,
                        'docker_tags': image.tags,
                        'description': f'Automatically discovered Docker image: {tag}'
                    }
                )
                
                if not created:
                    device.docker_id = image.id
                    device.docker_tags = image.tags
                    device.save()
                
                status = 'Created' if created else 'Updated'
                self.stdout.write(
                    self.style.SUCCESS(f'{status} device template: {device.name} {device.version}')
                )
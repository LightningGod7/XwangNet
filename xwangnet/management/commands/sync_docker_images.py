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
                # Keep the full image path to prevent duplicates
                name_parts = tag.split(':')
                full_name = name_parts[0]
                version = name_parts[1] if len(name_parts) > 1 else 'latest'
                
                device, created = DeviceTemplate.objects.get_or_create(
                    image=tag,  # Use full image tag as unique identifier
                    defaults={
                        'name': full_name.split('/')[-1],  # Still use short name for display
                        'version': version,
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
                    self.style.SUCCESS(f'{status} device template: {device.name} {device.version} ({tag})')
                )
import docker
import logging
from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)

def populate_webtop(sender, **kwargs):
    from .models import DeviceTemplate
    webtop_image = {
        'name': 'webtop',
        'version': 'latest',
        'image': "webtop:latest",
        'description': 'webtop client',
        'ports': {3000:None}
    }
    device, created = DeviceTemplate.objects.get_or_create(
        name=webtop_image['name'],
        version=webtop_image['version'],
        defaults={
            'image': webtop_image['image'],
            'description': webtop_image['description'],
            'ports': webtop_image['ports']
        }
    )
    # Build if not exist
    if not created:
        # check if image already in host
        logger.info(f"Building {device.image}")
        client = docker.from_env()
        try:
            image = client.images.get(webtop_image['image'])
            logger.debug(f"Image already exists: {image}")
        except docker.errors.ImageNotFound:
            image, _ = client.images.build(path=".", dockerfile="webtop.Dockerfile", tag="webtop:latest")
            logger.info(f"Built image: {image}")

class XwangnetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "xwangnet"


    def ready(self):
        post_migrate.connect(populate_webtop, sender=self)



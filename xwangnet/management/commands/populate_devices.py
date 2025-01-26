from django.core.management.base import BaseCommand
from xwangnet.models import DeviceTemplate

class Command(BaseCommand):
    help = 'Populates the database with initial device templates'

    def handle(self, *args, **kwargs):
        devices = [
            {
                'name': 'OpenWRT',
                'version': '24.10.0',
                'image': 'vrnetlab/vr-openwrt:24.10.0',
                'description': 'OpenWRT router with latest version'
            },
            {
                'name': 'OpenWRT',
                'version': '23.05.5',
                'image': 'vrnetlab/vr-openwrt:23.05.5',
                'description': 'OpenWRT router stable version'
            },
            {
                'name': 'OpenWRT',
                'version': '22.03.7',
                'image': 'vrnetlab/vr-openwrt:22.03.7',
                'description': 'OpenWRT router legacy version'
            },
            {
                'name': 'OpenWRT',
                'version': '21.02.7',
                'image': 'vrnetlab/vr-openwrt:21.02.7',
                'description': 'OpenWRT router older version'
            },
            {
                'name': 'OpenWRT',
                'version': '19.07.9',
                'image': 'vrnetlab/vr-openwrt:19.07.9',
                'description': 'OpenWRT router vintage version'
            },
            {
                'name': 'OpenWRT',
                'version': '18.06.9',
                'image': 'vrnetlab/vr-openwrt:18.06.9',
                'description': 'OpenWRT router classic version'
            }
        ]

        for device in devices:
            DeviceTemplate.objects.get_or_create(
                name=device['name'],
                version=device['version'],
                defaults={
                    'image': device['image'],
                    'description': device['description']
                }
            )
            self.stdout.write(
                self.style.SUCCESS(f'Successfully created device template: {device["name"]} {device["version"]}')
            ) 
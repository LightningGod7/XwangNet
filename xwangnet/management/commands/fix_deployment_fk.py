from django.core.management.base import BaseCommand
from django.db import connection
from xwangnet.models import Deployment, DeployedContainer

class Command(BaseCommand):
    help = 'Fixes foreign key constraints for deployments by cleaning up orphaned records'

    def handle(self, *args, **kwargs):
        self.stdout.write("Checking for orphaned DeployedContainer records...")
        
        # Find containers with invalid deployment references
        all_containers = DeployedContainer.objects.all()
        orphaned = []
        
        for container in all_containers:
            try:
                # Try to access the deployment
                _ = container.deployment.id
            except Deployment.DoesNotExist:
                orphaned.append(container)
        
        if orphaned:
            self.stdout.write(
                self.style.WARNING(f'Found {len(orphaned)} orphaned container records')
            )
            for container in orphaned:
                self.stdout.write(f"  - Container ID: {container.id}, Container: {container.container_id}")
                container.delete()
            self.stdout.write(
                self.style.SUCCESS(f'Deleted {len(orphaned)} orphaned container records')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('No orphaned records found. Database is clean!')
            )
        
        # Check for deployments with no containers
        self.stdout.write("\nChecking for deployments with no containers...")
        empty_deployments = Deployment.objects.filter(containers__isnull=True).distinct()
        
        if empty_deployments.exists():
            count = empty_deployments.count()
            self.stdout.write(
                self.style.WARNING(f'Found {count} deployments with no containers:')
            )
            for deployment in empty_deployments:
                self.stdout.write(f"  - Deployment ID: {deployment.id}, Name: {deployment.name}")
        else:
            self.stdout.write(
                self.style.SUCCESS('All deployments have containers.')
            )
        
        self.stdout.write("\n" + self.style.SUCCESS('Database consistency check complete!'))


from django.contrib import admin
from django.utils.html import format_html
from django.urls import path, reverse
from django.shortcuts import redirect
from django.contrib import messages
import docker
from .models import DeviceTemplate, NetworkConfiguration, DeviceInstance, Deployment, DeployedContainer

@admin.register(DeviceTemplate)
class DeviceTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'version', 'image', 'docker_status', 'created_at', 'custom_actions')
    search_fields = ('name', 'image', 'version', 'description')
    list_filter = ('created_at', 'version')
    readonly_fields = ('docker_id', 'docker_tags')
    actions = ['sync_with_docker_action']  # List of admin actions
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'version', 'description')
        }),
        ('Docker Configuration', {
            'fields': ('image', 'docker_id', 'docker_tags', 'build_instructions')
        }),
        ('Default Settings', {
            'fields': ('ports', 'environment'),
            'classes': ('collapse',)
        })
    )

    def docker_status(self, obj):
        if obj.sync_with_docker():
            return format_html('<span style="color: green;">✓ Available</span>')
        return format_html('<span style="color: red;">✗ Not Found</span>')
    docker_status.short_description = 'Docker Status'

    def custom_actions(self, obj):  # Renamed from 'actions' to 'custom_actions'
        return format_html(
            '<a class="button" href="{}">Pull Image</a> '
            '<a class="button" href="{}">Build Image</a>',
            f'/admin/pull-image/{obj.id}/',
            f'/admin/build-image/{obj.id}/'
        )
    custom_actions.short_description = 'Actions'

    def sync_with_docker_action(self, request, queryset):
        """Admin action to sync selected devices with Docker"""
        for device in queryset:
            if device.sync_with_docker():
                messages.success(request, f'Successfully synced {device.name} {device.version}')
            else:
                messages.error(request, f'Failed to sync {device.name} {device.version}')
    sync_with_docker_action.short_description = "Sync selected devices with Docker"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('pull-image/<int:device_id>/', 
                 self.admin_site.admin_view(self.pull_image),
                 name='pull-image'),
            path('build-image/<int:device_id>/',
                 self.admin_site.admin_view(self.build_image),
                 name='build-image'),
        ]
        return custom_urls + urls

    def pull_image(self, request, device_id):
        device = DeviceTemplate.objects.get(id=device_id)
        try:
            client = docker.from_env()
            client.images.pull(device.image)
            device.sync_with_docker()
            messages.success(request, f'Successfully pulled image: {device.image}')
        except Exception as e:
            messages.error(request, f'Error pulling image: {str(e)}')
        return redirect('admin:xwangnet_devicetemplate_changelist')

    def build_image(self, request, device_id):
        device = DeviceTemplate.objects.get(id=device_id)
        try:
            # Add your build logic here
            messages.info(request, f'Building image: {device.image}')
        except Exception as e:
            messages.error(request, f'Error building image: {str(e)}')
        return redirect('admin:xwangnet_devicetemplate_changelist')

@admin.register(NetworkConfiguration)
class NetworkConfigurationAdmin(admin.ModelAdmin):
    list_display = ('name', 'network_type', 'subnet', 'gateway', 'is_active')
    list_filter = ('network_type', 'is_active')
    search_fields = ('name',)

@admin.register(Deployment)
class DeploymentAdmin(admin.ModelAdmin):
    list_display = ('name', 'network_name', 'network_status', 'container_count', 'created_at')
    list_filter = ('network_status', 'created_at', 'network')
    search_fields = ('name', 'description', 'network__name')
    readonly_fields = ('created_at', 'network_status', 'docker_network_id')
    raw_id_fields = ('network',)
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'created_at')
        }),
        ('Network Configuration', {
            'fields': ('network', 'network_status', 'docker_network_id')
        }),
    )

    def network_name(self, obj):
        return obj.network.name if obj.network else '-'
    network_name.short_description = 'Network'
    network_name.admin_order_field = 'network__name'

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'network' in form.base_fields:
            form.base_fields['network'].label_from_instance = lambda obj: f"{obj.name} ({obj.subnet})"
        return form

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        deployment = self.get_object(request, object_id)
        extra_context['containers'] = deployment.containers.all()
        extra_context['show_network_controls'] = True
        return super().change_view(request, object_id, form_url, extra_context)

    def container_count(self, obj):
        return obj.containers.count()
    container_count.short_description = 'Containers'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:deployment_id>/toggle-network/',
                 self.admin_site.admin_view(self.toggle_network),
                 name='toggle-network'),
            path('<int:deployment_id>/container/<int:container_id>/action/',
                 self.admin_site.admin_view(self.container_action),
                 name='container-action'),
        ]
        return custom_urls + urls

    def toggle_network(self, request, deployment_id):
        deployment = self.get_object(request, deployment_id)
        action = request.GET.get('action')
        client = docker.from_env()
        
        try:
            if action == 'up' and deployment.network_status == 'down':
                network = client.networks.create(
                    deployment.network.name,
                    driver=deployment.network.network_type,
                    ipam=docker.types.IPAMConfig(
                        pool_configs=[
                            docker.types.IPAMPool(
                                subnet=deployment.network.subnet,
                                gateway=deployment.network.gateway
                            )
                        ]
                    )
                )
                deployment.docker_network_id = network.id
                deployment.network_status = 'up'
                deployment.save()
                messages.success(request, f'Network {deployment.network.name} started successfully')
            
            elif action == 'down' and deployment.network_status == 'up':
                network = client.networks.get(deployment.docker_network_id)
                network.remove()
                deployment.docker_network_id = None
                deployment.network_status = 'down'
                deployment.save()
                messages.success(request, f'Network {deployment.network.name} stopped successfully')
                
        except Exception as e:
            messages.error(request, f'Error: {str(e)}')
            
        return redirect('admin:xwangnet_deployment_change', deployment_id)

    def container_action(self, request, deployment_id, container_id):
        deployment = self.get_object(request, deployment_id)
        container = deployment.containers.get(id=container_id)
        action = request.GET.get('action')
        client = docker.from_env()
        
        try:
            if action == 'start' and deployment.network_status == 'up':
                # Get image ID
                try:
                    image = client.images.get(container.device.image)
                    image_id = image.id
                except docker.errors.ImageNotFound:
                    messages.error(request, f'Image {container.device.image} not found')
                    return redirect('admin:xwangnet_deployment_change', deployment_id)
                
                # Create container with image ID
                docker_container = client.containers.run(
                    image_id,
                    name=f"{container.hostname}-{container.id}",
                    hostname=container.hostname,
                    network=deployment.network.name,
                    environment=container.device.environment,
                    ports=container.device.ports,
                    detach=True,
                    remove=True
                )
                container.container_id = docker_container.id
                container.status = 'running'
                container.save()
                messages.success(request, f'Container {container.hostname} started successfully')
                
            elif action in ['stop', 'restart'] and container.container_id:
                docker_container = client.containers.get(container.container_id)
                if action == 'stop':
                    docker_container.stop()
                    container.status = 'stopped'
                else:
                    docker_container.restart()
                container.save()
                messages.success(request, f'Container {container.hostname} {action}ed successfully')
                
        except docker.errors.APIError as e:
            messages.error(request, f'Docker API Error: {str(e)}')
        except Exception as e:
            messages.error(request, f'Error: {str(e)}')
            
        return redirect('admin:xwangnet_deployment_change', deployment_id)

@admin.register(DeployedContainer)
class DeployedContainerAdmin(admin.ModelAdmin):
    list_display = ('hostname', 'deployment_name', 'device_name', 'status', 'container_id_short', 'created_at')
    list_filter = ('status', 'created_at', 'deployment')
    search_fields = ('hostname', 'deployment__name', 'device__name')
    readonly_fields = ('deployment', 'device', 'hostname', 'container_id', 'status', 'created_at')
    
    fieldsets = (
        ('Container Information', {
            'fields': ('hostname', 'status', 'container_id', 'created_at')
        }),
        ('Relationships', {
            'fields': ('deployment', 'device')
        }),
    )

    def deployment_name(self, obj):
        return obj.deployment.name
    deployment_name.short_description = 'Deployment'
    deployment_name.admin_order_field = 'deployment__name'
    
    def device_name(self, obj):
        return obj.device.name
    device_name.short_description = 'Device'
    device_name.admin_order_field = 'device__name'
    
    def container_id_short(self, obj):
        if obj.container_id:
            return obj.container_id[:12]
        return '-'
    container_id_short.short_description = 'Container ID'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

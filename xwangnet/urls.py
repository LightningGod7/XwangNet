from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('containers/', views.container_list, name='container_list'),
    path('compose-generator/', views.compose_generator, name='compose_generator'),
    path('devices/select/', views.device_selection, name='device_selection'),
    path('network/configure/', views.network_config, name='network_config'),
    path('compose/preview/', views.compose_preview, name='compose_preview'),
    path('compose/deploy/', views.deploy_compose, name='deploy_compose'),
    path('deployments/', views.deployment_list, name='deployment_list'),
    path('deployment/<int:deployment_id>/', views.deployment_detail, name='deployment_detail'),
    path('deployment/<int:deployment_id>/network/', views.toggle_network, name='toggle_network'),
    path('deployment/<int:deployment_id>/add-containers/', views.add_containers_to_deployment, name='add_containers_to_deployment'),
    path('container/<int:container_id>/action/', views.container_action, name='container_action'),
    path('container/<int:container_id>/logs/', views.container_logs, name='container-logs'),
    path('container/<int:container_id>/buttons/', views.container_buttons, name='container-buttons'),
    path('containers/<str:container_id>/remove/', views.remove_container, name='remove_container'),
    path('api/containers/', views.container_list_api, name='container_list_api'),
    path('deployed-container/<int:container_id>/delete/', views.delete_deployed_container, name='delete_deployed_container'),
    path('networks/', views.networks, name='networks'),
    path('networks/<str:network_id>/action/', views.network_action, name='network_action'),
    path('upload_firmware/', views.upload_firmware, name='upload_firmware'),
    path('deployment/<int:deployment_id>/deploy-snort/', views.deploy_snort, name='deploy_snort'),
    path('deployment/<int:deployment_id>/stop-snort/', views.stop_snort, name='stop_snort'),
    path('deployment/<int:deployment_id>/snort-logs/', views.get_snort_logs, name='snort_logs'),
]

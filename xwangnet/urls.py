from django.urls import path, re_path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('containers/', views.container_list, name='container_list'),
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
    path('container/<int:container_id>/logs/execv/', views.container_execv_logs, name='container-execv-logs'),
    path('container/<int:container_id>/logs/follow/', views.container_logs_follow, name='container-logs-follow'),
    path('container/<int:container_id>/buttons/', views.container_buttons, name='container-buttons'),
    path('containers/<str:container_id>/remove/', views.remove_container, name='remove_container'),
    path('api/containers/', views.container_list_api, name='container_list_api'),
    path('deployed-container/<int:container_id>/delete/', views.delete_deployed_container, name='delete_deployed_container'),
    path('networks/', views.networks, name='networks'),
    path('networks/<str:network_id>/action/', views.network_action, name='network_action'),
    path('upload_firmware/', views.upload_firmware, name='upload_firmware'),
    path('deployment/<int:deployment_id>/deploy-suricata/', views.deploy_suricata, name='deploy_suricata'),
    path('deployment/<int:deployment_id>/stop-suricata/', views.stop_suricata, name='stop_suricata'),
    path('deployment/<int:deployment_id>/suricata-logs/', views.get_suricata_logs, name='suricata_logs'),
    path('container/<str:container_id>/shells/', views.container_shells, name='container_shells'),
    # External IP Configuration API
    path('api/list-interfaces/', views.list_interfaces_api, name='list_interfaces_api'),
    path('api/validate-interface/', views.validate_interface_api, name='validate_interface_api'),
    path('api/deployment/<int:deployment_id>/', views.deployment_status_api, name='deployment_status_api'),
]

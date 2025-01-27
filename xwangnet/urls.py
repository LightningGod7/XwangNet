from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('networks/', views.network_list, name='network_list'),
    path('containers/', views.container_list, name='container_list'),
    path('compose-generator/', views.compose_generator, name='compose_generator'),
    path('devices/select/', views.device_selection, name='device_selection'),
    path('network/configure/', views.network_config, name='network_config'),
    path('compose/preview/', views.compose_preview, name='compose_preview'),
    path('compose/deploy/', views.deploy_compose, name='deploy_compose'),
    path('deployments/', views.deployment_list, name='deployment_list'),
    path('deployment/<int:deployment_id>/', views.deployment_detail, name='deployment_detail'),
    path('deployment/<int:deployment_id>/network/', views.toggle_network, name='toggle_network'),
    path('container/<int:container_id>/action/', views.container_action, name='container_action'),
    path('container/<int:container_id>/logs/', views.container_logs, name='container-logs'),
    path('container/<int:container_id>/buttons/', views.container_buttons, name='container-buttons'),
    path('containers/<str:container_id>/remove/', views.remove_container, name='remove_container'),
    path('api/containers/', views.container_list_api, name='container_list_api'),
    path('deployed-container/<int:container_id>/delete/', views.delete_deployed_container, name='delete_deployed_container'),
]

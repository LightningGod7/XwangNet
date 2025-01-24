from django.urls import path
from . import views

urlpatterns = [
    path('network/create/', views.create_network, name='create_network'),
    path('container/create/', views.create_container, name='create_container'),
    path('networks/', views.network_list, name='network_list'),
    path('containers/', views.container_list, name='container_list'),
]
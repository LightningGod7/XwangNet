from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/shell/(?P<container_id>[^/]+)/(?P<shell_type>[^/]+)$', consumers.ShellConsumer.as_asgi()),
] 
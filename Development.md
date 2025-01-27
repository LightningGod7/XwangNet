# XWangNet Development Documentation

## Overview
XWangNet is a Django-based application for managing Docker networks and containers, specifically designed for network device virtualization.

## Models

### DockerNetwork
Basic network configuration:
- name: Name of the network
- subnet: Subnet for the network
- docker_network_id: ID of the Docker network
- network_status: Status of the network (up/down)
- created_at: Date and time when the network was created
- updated_at: Date and time when the network was last updated

```
python:xwangnet/models.py
startLine: 3
endLine: 9
```

### DeviceTemplate
Template for network devices with Docker configurations:
- name: Name of the device
- image: Docker image for the device
- environment: Environment variables for the device
- ports: Ports to be exposed for the device
- created_at: Date and time when the device template was created
- updated_at: Date and time when the device template was last updated

```python:xwangnet/models.py
startLine: 21
endLine: 50
```
Key features:
- Version control for devices
- Docker image synchronization
- Port and environment variable management
- Build instructions storage

### NetworkConfiguration
Extended network configuration with additional features:
- Network status (up/down)
- Docker network ID
- Network configuration details
- Deployment history
- Container management
- Logs and status updates
- User-friendly interface for network management

```python:xwangnet/models.py
startLine: 52
endLine: 63
```
### Deployment
Represents a complete network deployment:


```python:xwangnet/models.py
startLine: 74
endLine: 83
```

## Views

### Deployment Management

#### deploy_compose
Creates new deployments:
```python:xwangnet/views.py
startLine: 128
endLine: 169
```

Features:
- Network configuration
- Device template selection
- Container creation
- Error handling

#### deployment_detail
Shows deployment information and controls:
```python:xwangnet/views.py
startLine: 183
endLine: 188
```

### Container Management

#### container_action
Handles container lifecycle:
```python:xwangnet/views.py
startLine: 284
endLine: 339
```

Features:
- Start/Stop/Restart containers
- Image ID-based deployment
- Automatic cleanup
- Status tracking

#### container_logs
Real-time container log retrieval:
```python:xwangnet/views.py
startLine: 341
endLine: 355
```

## Admin Interface

### DeploymentAdmin
```python:xwangnet/admin.py
startLine: 98
endLine: 102
```

Features:
- Network status management
- Container deployment controls
- Filtering and search capabilities

## Frontend Components

### Deployment List
Main deployment overview:
```html:xwangnet/templates/deployment_list.html
startLine: 12
endLine: 51
```

### Deployment Detail
Container management interface:
```html:xwangnet/templates/deployment_detail.html
startLine: 62
endLine: 104
```

## Development Guidelines

### Docker Operations
1. Always use image IDs instead of names:
```python
image = client.images.get(container.device.image)
image_id = image.id
```

2. Enable automatic container cleanup:
```python
client.containers.run(
    image_id,
    remove=True,  # Equivalent to --rm flag
    ...
)
```

### Error Handling
1. Docker API errors:
```python:xwangnet/views.py
startLine: 289
endLine: 299
```

2. Frontend error display:
```html:xwangnet/templates/deployment_detail.html
startLine: 173
endLine: 179
```

### Status Management
1. Update container status after operations
2. Maintain network state consistency
3. Handle edge cases (already removed containers)

### UI Interactions
1. Use AJAX for async operations
2. Show loading states
3. Display error messages
4. Update UI elements after successful operations

## API Endpoints

### Container Management
- POST `/container/{id}/action/`: Container lifecycle management
- GET `/container/{id}/logs/`: Container log retrieval
- GET `/container/{id}/buttons/`: Update container control buttons

### Deployment Management
- POST `/deployment/create/`: Create new deployment
- DELETE `/deployment/{id}/`: Remove deployment
- POST `/deployment/{id}/network/`: Toggle network status

### Network Management
- POST `/network/create/`: Create new network
  ```python:xwangnet/views.py
  startLine: 13
  endLine: 26
  ```
  - Parameters:
    - name: Network name
    - subnet: Network subnet (e.g., "172.16.0.0/24")
    - gateway: Network gateway IP

- GET `/network/list/`: List all networks
  ```python:xwangnet/views.py
  startLine: 47
  endLine: 59
  ```
  - Returns:
    - Network details
    - Connected containers
    - IP configurations
    - Network status

### Container Management
- POST `/container/{id}/action/`: Container lifecycle management
- GET `/container/{id}/logs/`: Container log retrieval
- GET `/container/{id}/buttons/`: Update container control buttons

### Deployment Management
- POST `/deployment/create/`: Create new deployment
- DELETE `/deployment/{id}/`: Remove deployment
- POST `/deployment/{id}/network/`: Toggle network status

## Network Operations

### Creating Networks

```python
client.networks.create(
name,
driver="bridge",
ipam={
'Config': [
{'Subnet': subnet, 'Gateway': gateway}
]
}
)
```
### Network Status Management
- Check network existence before creation
- Handle cleanup on failures
- Update status in database
- Maintain consistency with Docker daemon

### Error Handling
1. Network already exists
2. Invalid subnet/gateway configuration
3. Docker daemon connection issues
4. Permission errors
5. Resource conflicts

### Best Practices
1. Use unique network names
2. Validate IP ranges before creation
3. Clean up orphaned networks
4. Monitor network resource usage
5. Handle concurrent network operations

## Testing Guidelines

1. Test Docker operations with non-existent images
2. Verify cleanup of stopped containers
3. Check network state consistency
4. Validate error handling
5. Test concurrent operations

## Common Issues

1. Container name conflicts
   - Solution: Use unique names with IDs
   - Always enable automatic cleanup

2. Network state inconsistency
   - Check network existence before operations
   - Handle cleanup on failures

3. Missing Docker images
   - Verify image existence before container creation
   - Provide clear error messages

## Contributing

1. Follow PEP 8 style guide
2. Add docstrings for new functions
3. Update tests for new features
4. Document API changes
5. Handle error cases appropriately
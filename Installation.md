# Environment

- [ ] Ubuntu 24.04 LTS is recommended
- [ ] Python 3.11 and above is installed
- [ ] Docker is installed
- [ ] Docker Compose is installed
- [ ] User has docker access

For more information please follow [Docker Installation](https://docs.docker.com/engine/install/ubuntu/)

## Installing Dependencies

```shell
sudo apt install python3-pip
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Setting up Services

First, start the required services using Docker Compose:

```shell
# Start Caddy reverse proxy and other required services
docker-compose up -d
```

## Database Setup
```shell
python3 manage.py makemigrations
python3 manage.py migrate
python3 manage.py createsuperuser
python3 manage.py sync_docker_images
daphne xwangnetapp.asgi:application -b 0.0.0.0 -p 8000
```

## Running the Application

For development, you can use Daphne (ASGI server):

```shell
# Run the ASGI server
daphne -b 0.0.0.0 -p 8000 xwangnet.asgi:application
```

For production, it's recommended to:
1. Use a process manager (like supervisord)
2. Set up proper SSL termination
3. Configure proper security settings

## Verifying Installation

1. Access the admin interface at `http://localhost:8000/admin`
2. Log in with your superuser credentials
3. Verify that Docker images are synced
4. Check that Caddy proxy is running (`docker ps`)

## Troubleshooting

If you encounter issues:
1. Check Docker service status: `systemctl status docker`
2. Verify Docker Compose services: `docker-compose ps`
3. Check Daphne logs
4. Ensure all required ports are available (8000, 80, 443)
# Environment

- [ ] Ubuntu 24.04 LTS is recommended

- [ ] Python 3.11 and above is installed

- [ ] docker is installed

- [ ] user has docker access

For more information please follow [Docker Installation](https://docs.docker.com/engine/install/ubuntu/)

## Installing Dependencies

```shell
sudo apt install python3-pip
sudo apt install python3-virtualenv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Getting Started
```shell
python3 manage.py makemigrations
python3 manage.py migrate
python3 manage.py createsuperuser
python3 manage.py sync_docker_images
python3 manage.py runserver
```

# Environment

- [ ] Ubuntu 24.04 LTS is recommended

- [ ] Python 3.11 and above is installed

- [ ] docker is installed

- [ ] user has docker access

For more information please follow [Docker Installation](https://docs.docker.com/engine/install/ubuntu/)

## Installing Dependencies

```shell
sudo apt install pipx
pipx install poetry
pipx ensurepath
```

Restart terminal
```shell
poetry install
poetry run python manage.py makemigrations
poetry run python manage.py migrate
python manage.py runserver
```

### Development

To run any python code in poetry environment

```shell
poetry run python 
```
#### Updating add depedencies

```shell
poetry update
```

#### Adding python dependencies

```shell
poetry add <package>
```

####  Removing python dependencies
```shell
poetry remove <package>
```

#### Checking dependencies
```shell
poetry show
```

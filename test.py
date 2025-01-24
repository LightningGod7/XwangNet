import docker
client = docker.from_env()
print(client.ping())  # Should return True if the connection is successful
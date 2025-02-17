import json
from channels.generic.websocket import AsyncWebsocketConsumer
import docker
import asyncio
import paramiko

class ShellConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.docker_client = docker.from_env()
        self.socket = None
        self.qemu_ssh_client = None
        self.qemu_channel = None
        self.chroot_ssh_client = None
        self.chroot_channel = None
        self.shell_type = None

    async def connect(self):
        try:
            # Extract container ID and shell type from URL path
            path_parts = self.scope['url_route']['kwargs']
            container_id = path_parts['container_id']
            self.shell_type = path_parts['shell_type']

            # Get Docker container
            container = self.docker_client.containers.get(container_id)
            if container.status != 'running':
                await self.close()
                return

            # Accept the connection
            await self.accept()

            if self.shell_type == 'docker':
                await self.setup_docker_shell(container)
            elif self.shell_type == 'qemu':
                await self.setup_qemu_shell(container)
            elif self.shell_type == 'chroot':
                await self.setup_chroot_shell(container)

        except Exception as e:
            print(f"Connection error: {e}")
            await self.close()

    async def setup_docker_shell(self, container):
        # Create exec instance
        exec_id = container.client.api.exec_create(
            container.id,
            '/bin/sh',
            stdin=True,
            tty=True
        )['Id']
        
        # Start the exec instance
        self.socket = container.client.api.exec_start(
            exec_id,
            socket=True,
            tty=True,
            stream=True
        )._sock

        # Set socket to non-blocking mode
        self.socket.setblocking(False)

        # Start reading output in background
        asyncio.create_task(self.read_docker_output())

    async def setup_qemu_shell(self, container):
        # Get container IP
        networks = container.attrs['NetworkSettings']['Networks'].values()
        container_ip = next(iter(networks))['IPAddress']

        # Setup SSH client for QEMU
        self.qemu_ssh_client = paramiko.SSHClient()
        self.qemu_ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.qemu_ssh_client.connect(container_ip, username='root', password='')

        # Execute nochroot command for QEMU
        stdin, stdout, stderr = self.qemu_ssh_client.exec_command("nochroot")
        
        # Get shell channel
        self.qemu_channel = self.qemu_ssh_client.invoke_shell()
        self.qemu_channel.setblocking(0)

        # Start reading output in background
        asyncio.create_task(self.read_qemu_output())

    async def setup_chroot_shell(self, container):
        # Get container IP
        networks = container.attrs['NetworkSettings']['Networks'].values()
        container_ip = next(iter(networks))['IPAddress']

        # Setup SSH client for chroot
        self.chroot_ssh_client = paramiko.SSHClient()
        self.chroot_ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.chroot_ssh_client.connect(container_ip, username='root', password='')

        # Get shell channel
        self.chroot_channel = self.chroot_ssh_client.invoke_shell()
        self.chroot_channel.setblocking(0)

        # Start reading output in background
        asyncio.create_task(self.read_chroot_output())

    async def disconnect(self, close_code):
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        if self.qemu_channel:
            try:
                self.qemu_channel.close()
            except:
                pass
        if self.qemu_ssh_client:
            try:
                self.qemu_ssh_client.close()
            except:
                pass
        if self.chroot_channel:
            try:
                self.chroot_channel.close()
            except:
                pass
        if self.chroot_ssh_client:
            try:
                self.chroot_ssh_client.close()
            except:
                pass

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            
            if 'type' in data and data['type'] == 'resize':
                # Handle resize events if needed
                return
                
            if 'input' in data:
                command = data['input']
                if self.shell_type == 'docker' and self.socket:
                    self.socket.send(command.encode())
                elif self.shell_type == 'qemu' and self.qemu_channel:
                    self.qemu_channel.send(command)
                elif self.shell_type == 'chroot' and self.chroot_channel:
                    self.chroot_channel.send(command)
                
        except Exception as e:
            print(f"Error handling message: {e}")

    async def read_docker_output(self):
        try:
            while True:
                try:
                    output = self.socket.recv(1024)
                    if output:
                        await self.send(text_data=output.decode())
                except BlockingIOError:
                    await asyncio.sleep(0.01)
                except Exception as e:
                    print(f"Error reading docker output: {e}")
                    break

        except Exception as e:
            print(f"Error in docker output loop: {e}")
            await self.close()

    async def read_qemu_output(self):
        try:
            while True:
                if self.qemu_channel and self.qemu_channel.recv_ready():
                    output = self.qemu_channel.recv(1024)
                    if output:
                        await self.send(text_data=output.decode())
                await asyncio.sleep(0.01)

        except Exception as e:
            print(f"Error in QEMU output loop: {e}")
            await self.close()

    async def read_chroot_output(self):
        try:
            while True:
                if self.chroot_channel and self.chroot_channel.recv_ready():
                    output = self.chroot_channel.recv(1024)
                    if output:
                        await self.send(text_data=output.decode())
                await asyncio.sleep(0.01)

        except Exception as e:
            print(f"Error in Chroot output loop: {e}")
            await self.close() 
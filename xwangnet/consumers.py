import json
from channels.generic.websocket import AsyncWebsocketConsumer
import docker
import asyncio
import paramiko
import os
from django.conf import settings

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
        self.read_task = None

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
        try:
            # Create exec instance with bash instead of sh for better history support
            exec_id = container.client.api.exec_create(
                container.id,
                '/bin/bash',  # Use bash instead of sh
                stdin=True,
                tty=True,
                environment={"TERM": "xterm"}  # Set TERM for better terminal support
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
            self.read_task = asyncio.create_task(self.read_docker_output())
        except Exception as e:
            print(f"Error setting up docker shell: {e}")
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None
            await self.close()

    async def setup_qemu_shell(self, container):
        try:
            # Get container IP
            networks = container.attrs['NetworkSettings']['Networks'].values()
            container_ip = next(iter(networks))['IPAddress']
            
            if not container_ip:
                print(f"No IP address found for container")
                await self.close()
                return

            # Setup SSH client for QEMU
            self.qemu_ssh_client = paramiko.SSHClient()
            self.qemu_ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            try:
                self.qemu_ssh_client.connect(
                    container_ip,
                    username='root',
                    password='root',
                    timeout=10
                )
            except paramiko.ssh_exception.AuthenticationException:
                print("Authentication failed")
                await self.close()
                return
            except Exception as e:
                print(f"SSH connection error for QEMU: {str(e)}")
                await self.close()
                return

            # Execute nochroot command for QEMU
            try:
                stdin, stdout, stderr = self.qemu_ssh_client.exec_command("nochroot")
            except Exception as e:
                print(f"Error executing nochroot command: {str(e)}")
                await self.close()
                return
            
            # Get shell channel with terminal type set
            try:
                self.qemu_channel = self.qemu_ssh_client.invoke_shell(term='xterm')
                self.qemu_channel.setblocking(0)
            except Exception as e:
                print(f"Error setting up shell channel: {str(e)}")
                await self.close()
                return

            # Start reading output in background
            asyncio.create_task(self.read_qemu_output())
            
        except Exception as e:
            print(f"Error in setup_qemu_shell: {str(e)}")
            if self.qemu_ssh_client:
                self.qemu_ssh_client.close()
            await self.close()

    async def setup_chroot_shell(self, container):
        try:
            # Get container IP
            networks = container.attrs['NetworkSettings']['Networks'].values()
            container_ip = next(iter(networks))['IPAddress']
            
            if not container_ip:
                print(f"No IP address found for container")
                await self.close()
                return

            # Setup SSH client for chroot
            self.chroot_ssh_client = paramiko.SSHClient()
            self.chroot_ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            try:
                self.chroot_ssh_client.connect(
                    container_ip,
                    username='root',
                    password='root',
                    timeout=10
                )
            except paramiko.ssh_exception.AuthenticationException:
                print("Authentication failed")
                await self.close()
                return
            except Exception as e:
                print(f"SSH connection error for Chroot: {str(e)}")
                await self.close()
                return

            # Get shell channel with terminal type set
            try:
                self.chroot_channel = self.chroot_ssh_client.invoke_shell(term='xterm')
                self.chroot_channel.setblocking(0)
                
                # Wait for channel to be ready
                await asyncio.sleep(1)
                
                # Execute chroot command immediately after connection
                chroot_cmd = "chroot /root/rootfs /bin/sh -i\n"
                print(f"Sending chroot command: {chroot_cmd}")
                self.chroot_channel.send(chroot_cmd)
                await asyncio.sleep(0.5)  # Wait for command to be processed
                
            except Exception as e:
                print(f"Error setting up shell channel: {str(e)}")
                await self.close()
                return

            # Start reading output in background
            asyncio.create_task(self.read_chroot_output())
            
        except Exception as e:
            print(f"Error in setup_chroot_shell: {str(e)}")
            if self.chroot_ssh_client:
                self.chroot_ssh_client.close()
            await self.close()

    async def disconnect(self, close_code):
        # Cancel the read task if it exists
        if self.read_task:
            self.read_task.cancel()
            try:
                await self.read_task
            except asyncio.CancelledError:
                pass

        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

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
            if not self.socket and not self.qemu_channel and not self.chroot_channel:
                return

            data = json.loads(text_data)
            
            if 'type' in data and data['type'] == 'resize':
                # Handle resize events
                size = data.get('size', {})
                if self.shell_type == 'qemu' and self.qemu_channel:
                    self.qemu_channel.resize_pty(width=size.get('cols', 80), height=size.get('rows', 24))
                elif self.shell_type == 'chroot' and self.chroot_channel:
                    self.chroot_channel.resize_pty(width=size.get('cols', 80), height=size.get('rows', 24))
                return
                
            if 'input' in data:
                command = data['input']
                if self.shell_type == 'docker' and self.socket:
                    try:
                        self.socket.send(command.encode())
                    except Exception as e:
                        print(f"Error sending to docker socket: {e}")
                        await self.close()
                elif self.shell_type == 'qemu' and self.qemu_channel:
                    self.qemu_channel.send(command)
                elif self.shell_type == 'chroot' and self.chroot_channel:
                    self.chroot_channel.send(command)
                
        except Exception as e:
            print(f"Error handling message: {e}")

    async def read_docker_output(self):
        try:
            while True:
                if not self.socket:
                    break

                try:
                    output = self.socket.recv(1024)
                    if output:
                        await self.send(text_data=output.decode())
                    else:
                        # Connection closed by the server
                        break
                except BlockingIOError:
                    await asyncio.sleep(0.01)
                except Exception as e:
                    print(f"Error reading docker output: {e}")
                    break

        except Exception as e:
            print(f"Error in docker output loop: {e}")
        finally:
            # Ensure we close the connection when the read loop ends
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
            print("Starting chroot output reader")
            while True:
                if self.chroot_channel and self.chroot_channel.recv_ready():
                    output = self.chroot_channel.recv(1024)
                    if output:
                        decoded_output = output.decode()
                        print(f"Received chroot output: {decoded_output}")
                        await self.send(text_data=decoded_output)
                await asyncio.sleep(0.01)

        except Exception as e:
            print(f"Error in Chroot output loop: {str(e)}")
            await self.close() 

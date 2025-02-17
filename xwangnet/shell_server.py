import asyncio
import websockets
import json
import docker
import paramiko
from threading import Thread
from queue import Queue
import io


class ShellServer:
    def __init__(self):
        self.docker_client = docker.from_env()
        self.connections = {}

    async def handle_websocket(self, websocket):

        try:
            # Get path from the request instead of websocket
            path = websocket.request.path
            
            # Verify the WebSocket path
            if not path.startswith('/ws/shell/'):
                await websocket.close()
                return

            # Extract container ID and shell type
            path = path.replace('/ws/shell/', '')
            parts = path.strip('/').split('/')
            if len(parts) != 2:
                await websocket.close()
                return

            container_id, shell_type = parts
            print(f"Handling shell connection for container: {container_id}, type: {shell_type}")

            # Get Docker container
            container = self.docker_client.containers.get(container_id)
            if container.status != 'running':
                await websocket.close()
                return

            if shell_type == 'docker':
                await self.handle_docker_shell(websocket, container)
            elif shell_type in ['qemu', 'chroot']:
                await self.handle_ssh_shell(websocket, container, shell_type)

        except Exception as e:
            print(f"Error: {e}")
            try:
                await websocket.send(json.dumps({"error": str(e)}))
            except:
                pass
            finally:
                await websocket.close()

    async def handle_docker_shell(self, websocket, container):
        try:
            # Create exec instance using the low-level API
            exec_id = container.client.api.exec_create(
                container.id,
                '/bin/sh',
                stdin=True,
                tty=True
            )['Id']
            
            # Start the exec instance in attached mode
            socket = container.client.api.exec_start(
                exec_id,
                socket=True,
                tty=True,
                stream=True
            )._sock

            # Set socket to non-blocking mode
            socket.setblocking(False)

            try:
                while True:
                    # Handle WebSocket messages
                    try:
                        message = await websocket.recv()
                        data = json.loads(message)
                        
                        if 'type' in data and data['type'] == 'resize':
                            # Handle resize events if needed
                            continue
                            
                        if 'input' in data:
                            # Send the input to the container
                            command = data['input']
                            socket.send(command.encode())
                            
                            # Small delay to allow output to be processed
                            await asyncio.sleep(0.05)
                            
                    except websockets.exceptions.ConnectionClosed:
                        break
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error: {e}")
                        continue
                    except Exception as e:
                        print(f"Error handling message: {e}")
                        continue

                    # Read any available output
                    try:
                        while True:
                            try:
                                output = socket.recv(1024)
                                #strip first line of output if not have duplicate output
                                output = output.decode().split('\n')[1:]
                                output = '\n'.join(output)
                                if output:
                                    await websocket.send(output)
                                else:
                                    break
                            except BlockingIOError:
                                # No more data available
                                break
                            except Exception as e:
                                print(f"Error reading output: {e}")
                                break
                    except Exception as e:
                        print(f"Error in output loop: {e}")
                        break

                    # Small delay between checks
                    await asyncio.sleep(0.01)

            finally:
                try:
                    socket.close()
                except:
                    pass

        except Exception as e:
            print(f"Shell error: {e}")
            await websocket.send(f"Error: {str(e)}")
        finally:
            await websocket.close()

    async def handle_ssh_shell(self, websocket, container, shell_type):
        try:
            # Get container IP - Fix for dict_values not being subscriptable
            networks = container.attrs['NetworkSettings']['Networks'].values()
            container_ip = next(iter(networks))['IPAddress']

            # Setup SSH client
            ssh_client = paramiko.SSHClient()
            # Load private key
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # Connect with appropriate command
            if shell_type == 'qemu':
                ssh_client.connect(container_ip, username='root', password='')
                stdin, stdout, stderr = ssh_client.exec_command("nochroot")
            else:  # chroot
                ssh_client.connect(container_ip, username='root', password='')

            # Get shell channel
            channel = ssh_client.invoke_shell()
            channel.setblocking(0)

            try:
                while True:
                    # Handle WebSocket messages
                    try:
                        message = await websocket.recv()
                        data = json.loads(message)
                        channel.send(data['input'])
                    except websockets.exceptions.ConnectionClosed:
                        break
                    except json.JSONDecodeError:
                        continue

                    # Check for shell output
                    if channel.recv_ready():
                        output = channel.recv(1024)
                        if output:
                            await websocket.send(output.decode())

                    # Small delay to prevent CPU overuse
                    await asyncio.sleep(0.01)

            finally:
                channel.close()
                ssh_client.close()

        except Exception as e:
            await websocket.send(f"Error: {str(e)}")
        finally:
            await websocket.close()

async def handle_websocket(websocket):
    shell_server = ShellServer()
    await shell_server.handle_websocket(websocket)

async def run_shell_server(host='0.0.0.0', port=8001):
    server = await websockets.serve(
        handle_websocket,
        host,
        port
    )
    print(f"Shell WebSocket server running on ws://{host}:{port}")
    await asyncio.Future()  # Keep running
#!/usr/bin/env python
import asyncio
from xwangnet.shell_server import run_shell_server

async def main():
    await run_shell_server(host='0.0.0.0', port=8001)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down shell server...") 
"""Local correctness/load smoke for middleware + WebSockets on real workers.

Run in the organization validation environment. Results characterize this host,
not production capacity. Uses ephemeral localhost ports and temporary app files.
"""

import argparse
import asyncio
import importlib.metadata
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

APPLICATION = """
from uuid import uuid4
from wred import App
from wred_middleware import middleware_application
from wred_websockets import WebSockets
api = App()
@api.route('/health')
async def health(request):
    return {'ok': True}
@api.route('/items/{item}')
async def item(request, item):
    return {'item': item}
sockets = WebSockets(api, allowed_origins=['https://load.example'],
                     max_connections=256, receive_timeout=30)
@sockets.route('/echo/{room}')
async def echo(websocket, room):
    await websocket.accept()
    async for data in websocket.iter_text():
        await websocket.send_text(room + ':' + data)
async def identify(scope):
    scope['load.id'] = uuid4().hex
async def headers(scope, response):
    response.headers.append(('X-Request-ID', scope['load.id']))
app = middleware_application(sockets, before=identify, after=headers)
"""


async def exercise(options, port):
    latencies = []
    started = time.perf_counter()

    async def socket_client(index):
        async with connect(
            f"ws://127.0.0.1:{port}/echo/{index}",
            origin="https://load.example",
            open_timeout=10,
            max_size=65536,
        ) as connection:
            for number in range(options.messages):
                tick = time.perf_counter()
                await connection.send(str(number))
                result = await asyncio.wait_for(connection.recv(), 10)
                if result != f"{index}:{number}":
                    raise RuntimeError("WebSocket data crossed connections or was lost")
                latencies.append(time.perf_counter() - tick)

    ids = set()
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", timeout=10
    ) as client:
        queue = asyncio.Queue()
        for item in range(options.requests):
            queue.put_nowait(item)

        async def http_client():
            while not queue.empty():
                item = queue.get_nowait()
                response = await client.get(f"/items/{item}")
                if response.status_code != 200 or response.json() != {
                    "item": str(item)
                }:
                    raise RuntimeError("Incorrect HTTP response")
                request_id = response.headers["x-request-id"]
                if request_id in ids:
                    raise RuntimeError("Request state crossed HTTP requests")
                ids.add(request_id)

        async with asyncio.TaskGroup() as tasks:
            for index in range(options.connections):
                tasks.create_task(socket_client(index))
            for _ in range(min(50, options.requests)):
                tasks.create_task(http_client())
    return {
        "workers": options.workers,
        "connections": options.connections,
        "websocket_messages": len(latencies),
        "http_requests": len(ids),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "websocket_roundtrip_median_ms": round(statistics.median(latencies) * 1000, 3),
        "websocket_roundtrip_p95_ms": round(
            sorted(latencies)[int(len(latencies) * 0.95)] * 1000, 3
        ),
        "versions": {
            name: importlib.metadata.version(name)
            for name in (
                "wred",
                "wred-middleware",
                "wred-websockets",
                "starlette",
                "uvicorn",
                "websockets",
            )
        },
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in [
        ("workers", 2),
        ("connections", 100),
        ("messages", 100),
        ("requests", 2000),
    ]:
        parser.add_argument("--" + name, type=int, default=default)
    options = parser.parse_args()
    if any(value <= 0 for value in vars(options).values()):
        parser.error("All counts must be positive")
    with tempfile.TemporaryDirectory(prefix="wred-transport-load-") as directory:
        root = Path(directory)
        (root / "load_app.py").write_text(APPLICATION)
        with socket.socket() as listener, (root / "server.log").open("w+") as log:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            process = await asyncio.to_thread(
                subprocess.Popen,
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "load_app:app",
                    "--app-dir",
                    str(root),
                    "--fd",
                    str(listener.fileno()),
                    "--workers",
                    str(options.workers),
                    "--lifespan",
                    "off",
                    "--ws",
                    "websockets",
                    "--ws-max-size",
                    "65536",
                    "--ws-max-queue",
                    "8",
                    "--ws-per-message-deflate",
                    "false",
                    "--log-level",
                    "warning",
                ],
                pass_fds=(listener.fileno(),),
                stdout=log,
                stderr=log,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            try:
                async with (
                    httpx.AsyncClient(timeout=0.3) as client,
                    asyncio.timeout(20),
                ):
                    while True:
                        if process.poll() is not None:
                            log.seek(0)
                            raise RuntimeError(log.read())
                        try:
                            response = await client.get(
                                f"http://127.0.0.1:{port}/health"
                            )
                            if response.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(0.05)
                async with asyncio.timeout(120):
                    print(json.dumps(await exercise(options, port), indent=2))
            finally:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)


if __name__ == "__main__":
    asyncio.run(main())

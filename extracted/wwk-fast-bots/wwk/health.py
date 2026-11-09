"""Мини HTTP-сервер /healthz на стандартной библиотеке (для проверки контейнера)."""
from __future__ import annotations

import asyncio
import json
from typing import Callable


class HealthServer:
    def __init__(self, port: int, status: Callable[[], dict], host: str = "0.0.0.0") -> None:
        self._port, self._status, self._host = port, status, host
        self._server: asyncio.AbstractServer | None = None

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1] if self._server else self._port

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            while (await asyncio.wait_for(reader.readline(), timeout=5)) not in (b"\r\n", b"\n", b""):
                pass
            path = line.decode("latin1").split(" ")[1] if line.count(b" ") >= 2 else "/"
            if path.split("?")[0] in ("/", "/healthz"):
                body = json.dumps(self._status()).encode()
                head = "HTTP/1.1 200 OK"
            else:
                body, head = b'{"error":"not found"}', "HTTP/1.1 404 Not Found"
            writer.write(f"{head}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self._host, self._port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

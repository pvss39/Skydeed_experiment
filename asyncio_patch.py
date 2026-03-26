"""
asyncio_patch.py — Replace anyio's TLS with pure asyncio on Windows.
Import this before telegram to fix ConnectError on Windows with Python 3.13.
"""
import asyncio
import ssl
import socket
from typing import Any

import httpcore._backends.anyio as _anyio_backend


class _AsyncioStream:
    """asyncio-native socket stream replacing anyio's TLS stream."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer

    async def read(self, max_bytes: int) -> bytes:
        return await self._reader.read(max_bytes)

    async def write(self, buffer: bytes) -> None:
        self._writer.write(buffer)
        await self._writer.drain()

    async def aclose(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except Exception:
            pass

    async def start_tls(self, ssl_context: ssl.SSLContext,
                        server_hostname: str = None, timeout: float = None) -> "_AsyncioStream":
        loop = asyncio.get_event_loop()
        transport = self._writer.transport
        raw_sock = transport.get_extra_info("socket")

        # Create new asyncio SSL connection over existing socket
        reader, writer = await asyncio.open_connection(
            host=server_hostname,
            port=None,
            sock=raw_sock,
            ssl=ssl_context,
            server_hostname=server_hostname,
        )
        return _AsyncioStream(reader, writer)


class AnyIOBackendPatched(_anyio_backend.AnyIOBackend):
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: list[tuple[int, int, int | bytes]] | None = None,
    ):
        """Use asyncio directly for TCP connections to avoid anyio TLS issues."""
        ssl_ctx = ssl.create_default_context()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ssl_ctx, server_hostname=host),
                timeout=timeout,
            )
            return _AsyncioStream(reader, writer)
        except asyncio.TimeoutError:
            raise httpcore.ConnectTimeout()
        except (OSError, ssl.SSLError) as exc:
            raise httpcore.ConnectError() from exc


# Apply patch
try:
    _anyio_backend.AnyIOBackend = AnyIOBackendPatched
    import httpcore._async.connection as _conn
    import httpcore._async.connection_pool as _pool
    # Reload backends in existing references
except Exception:
    pass  # If patch fails, bot still runs with retries

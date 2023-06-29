import asyncio
import pathlib
import socket
import sys
import unittest
from typing import Optional

import aioloop_proxy

_loop: Optional[asyncio.AbstractEventLoop] = None


def setUpModule() -> None:
    global _loop
    _loop = asyncio.new_event_loop()


def tearDownModule() -> None:
    global _loop
    assert _loop is not None
    if hasattr(_loop, "shutdown_default_executor"):
        _loop.run_until_complete(_loop.shutdown_default_executor())
    _loop.run_until_complete(_loop.shutdown_asyncgens())
    _loop.close()
    _loop = None


class TestSockOps(unittest.TestCase):
    def setUp(self) -> None:
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self) -> None:
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_sock_recv(self) -> None:
        async def f() -> None:
            async def serve(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                writer.write(b"DATA")
                writer.close()

            server = await asyncio.start_server(serve, "127.0.0.1", 0)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            await self.loop.sock_connect(sock, server.sockets[0].getsockname())
            data = await self.loop.sock_recv(sock, 1024)
            self.assertEqual(data, b"DATA")
            sock.close()
            server.close()
            await server.wait_closed()

        self.loop.run_until_complete(f())

    def test_sock_recv_into(self) -> None:
        async def f() -> None:
            async def serve(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                writer.write(b"DATA")
                writer.close()

            server = await asyncio.start_server(serve, "127.0.0.1", 0)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            await self.loop.sock_connect(sock, server.sockets[0].getsockname())
            buf = bytearray(1024)
            size = await self.loop.sock_recv_into(sock, buf)
            self.assertEqual(buf[:size], b"DATA")
            sock.close()
            server.close()
            await server.wait_closed()

        self.loop.run_until_complete(f())

    def test_sock_sendall(self) -> None:
        async def f() -> None:
            async def serve(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                data = await reader.read(1024)
                writer.write(b"ACK:" + data)
                writer.close()

            server = await asyncio.start_server(serve, "127.0.0.1", 0)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            await self.loop.sock_connect(sock, server.sockets[0].getsockname())
            await self.loop.sock_sendall(sock, b"DATA")
            data = await self.loop.sock_recv(sock, 1024)
            self.assertEqual(data, b"ACK:DATA")
            sock.close()
            server.close()
            await server.wait_closed()

        self.loop.run_until_complete(f())

    def test_sock_accept(self) -> None:
        async def f() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)

            reader, writer = await asyncio.open_connection(*sock.getsockname())

            conn, addr = await self.loop.sock_accept(sock)
            await self.loop.sock_sendall(conn, b"DATA")
            data = await reader.read(1024)
            self.assertEqual(data, b"DATA")
            conn.close()
            sock.close()
            writer.close()
            await writer.wait_closed()

        self.loop.run_until_complete(f())

    @unittest.skipIf(
        sys.platform == "win32" and sys.version_info < (3, 8),
        "sendfile is buggy for Python 3.7 on Windows",
    )
    def test_sock_sendfile(self) -> None:
        async def f() -> None:
            async def serve(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                data = await reader.read(0x1_000_000)
                writer.write(b"ACK:" + data)
                writer.close()

            server = await asyncio.start_server(serve, "127.0.0.1", 0)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            await self.loop.sock_connect(sock, server.sockets[0].getsockname())
            fname = pathlib.Path(__file__)
            with fname.open("rb") as fp:
                await self.loop.sock_sendfile(sock, fp)
            data = await self.loop.sock_recv(sock, 0x1_000_000)
            self.assertEqual(data, b"ACK:" + fname.read_bytes())
            sock.close()
            server.close()
            await server.wait_closed()

        self.loop.run_until_complete(f())


if __name__ == "__main__":
    unittest.main()

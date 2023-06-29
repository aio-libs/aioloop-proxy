from __future__ import annotations

import asyncio
import socket
import unittest
from typing import Any

import aioloop_proxy

_loop: asyncio.AbstractEventLoop | None = None


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


class DatagramProto(asyncio.DatagramProtocol):
    def __init__(self, case: TestDatagram) -> None:
        self.case = case
        self.loop = case.loop
        self.transp: asyncio.BaseTransport | None = None
        self._recv: asyncio.Future[tuple[bytes, Any]] = self.loop.create_future()
        self.events: set[str] = set()
        self.closed = self.loop.create_future()

    def connection_made(self, transp: asyncio.BaseTransport) -> None:
        self.transp = transp
        self.events.add("MADE")

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self._recv.set_result((data, addr))
        self.events.add("DATA")

    def error_received(self, exc: BaseException) -> None:
        self._recv.set_exception(exc)
        self.events.add("ERROR")

    def connection_lost(self, exc: BaseException | None) -> None:
        self.transp = None
        self.events.add("LOST")
        if exc is None:
            self.closed.set_result(None)
        else:
            self.closed.set_exception(exc)

    async def recv(self) -> tuple[bytes, Any]:
        try:
            return await self._recv
        finally:
            self._recv = self.loop.create_future()


class TestDatagram(unittest.TestCase):
    def setUp(self) -> None:
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self) -> None:
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_sendto(self) -> None:
        async def f() -> None:
            tr1, pr1 = await self.loop.create_datagram_endpoint(
                lambda: DatagramProto(self),
                family=socket.AF_INET,
                local_addr=("127.0.0.1", 0),
            )
            addr1 = tr1.get_extra_info("socket").getsockname()
            tr2, pr2 = await self.loop.create_datagram_endpoint(
                lambda: DatagramProto(self),
                family=socket.AF_INET,
                local_addr=("127.0.0.1", 0),
            )
            addr2 = tr2.get_extra_info("socket").getsockname()

            tr1.sendto(b"DATA", addr2)
            data, addr = await pr2.recv()
            self.assertEqual(data, b"DATA")
            self.assertEqual(addr, addr1)

            tr1.close()
            tr2.close()
            await pr1.closed
            await pr2.closed

        self.loop.run_until_complete(f())

    def test_error(self) -> None:
        async def f() -> None:
            tr1, pr1 = await self.loop.create_datagram_endpoint(
                lambda: DatagramProto(self),
                family=socket.AF_INET,
                local_addr=("127.0.0.1", 0),
            )

            tr1.sendto(b"DATA", ("127.0.0.0", 1))
            with self.assertRaises(OSError):
                await pr1.recv()

            tr1.close()
            await pr1.closed

        self.loop.run_until_complete(f())

    def test_abort(self) -> None:
        async def f() -> None:
            tr1, pr1 = await self.loop.create_datagram_endpoint(
                lambda: DatagramProto(self),
                family=socket.AF_INET,
                local_addr=("127.0.0.1", 0),
            )

            tr1.abort()
            await pr1.closed

        self.loop.run_until_complete(f())


if __name__ == "__main__":
    unittest.main()

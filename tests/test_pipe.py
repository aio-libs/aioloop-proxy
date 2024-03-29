from __future__ import annotations

import asyncio
import os
import sys
import unittest

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


class Proto(asyncio.Protocol):
    def __init__(self, case: TestPipes) -> None:
        self.case = case
        self.loop = case.loop
        self.transp: asyncio.BaseTransport | None = None
        self._recv: asyncio.Future[bytes] = self.loop.create_future()
        self.events: set[str] = set()
        self.closed = self.loop.create_future()

    def connection_made(self, transp: asyncio.BaseTransport) -> None:
        self.transp = transp
        self.events.add("MADE")

    def data_received(self, data: bytes) -> None:
        self._recv.set_result(data)
        self.events.add("DATA")

    def eof_received(self) -> None:
        self.events.add("EOF")

    def connection_lost(self, exc: BaseException | None) -> None:
        self.transp = None
        self.events.add("LOST")
        if exc is None:
            self.closed.set_result(None)
        else:
            self.closed.set_exception(exc)

    async def recv(self) -> bytes:
        try:
            return await self._recv
        finally:
            self._recv = self.loop.create_future()


@unittest.skipIf(sys.platform == "win32", "Not supported by Windows")
class TestPipes(unittest.TestCase):
    def setUp(self) -> None:
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self) -> None:
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_pipes(self) -> None:
        async def f() -> None:
            rpipe, wpipe = os.pipe()
            rpipeobj = open(rpipe, "rb", 1024)
            wpipeobj = open(wpipe, "wb", 1024)

            tr1, pr1 = await self.loop.connect_read_pipe(lambda: Proto(self), rpipeobj)
            tr2, pr2 = await self.loop.connect_write_pipe(lambda: Proto(self), wpipeobj)

            tr2.write(b"DATA\n")
            data = await pr1.recv()
            self.assertEqual(data, b"DATA\n")

            tr1.close()
            tr2.close()
            await pr1.closed
            await pr2.closed

        self.loop.run_until_complete(f())


if __name__ == "__main__":
    unittest.main()

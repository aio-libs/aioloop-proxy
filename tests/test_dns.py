import asyncio
import socket
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


class TestDNS(unittest.TestCase):
    def setUp(self) -> None:
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self) -> None:
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_getaddrinfo(self) -> None:
        async def f() -> None:
            addr = await self.loop.getaddrinfo(
                "example.org", 80, proto=socket.IPPROTO_TCP
            )
            self.assertListEqual(addr, expected)

        expected = socket.getaddrinfo("example.org", 80, proto=socket.IPPROTO_TCP)

        self.loop.run_until_complete(f())

    def test_getnameinfo(self) -> None:
        async def f() -> None:
            info = await self.loop.getnameinfo(addr, 0)
            self.assertEqual(info, expected)

        addrs = socket.getaddrinfo("example.org", 80, proto=socket.IPPROTO_TCP)
        addr: tuple[str, int] = tuple(addrs[0][4][:2])  # type: ignore[assignment]
        expected = socket.getnameinfo(addr, 0)

        self.loop.run_until_complete(f())

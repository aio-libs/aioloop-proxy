import asyncio
import unittest

import aioloop_proxy

_loop = None


def setUpModule():
    global _loop
    _loop = asyncio.new_event_loop()


def tearDownModule():
    global _loop
    _loop.run_until_complete(_loop.shutdown_default_executor())
    _loop.run_until_complete(_loop.shutdown_asyncgens())
    _loop.close()
    _loop = None


class TestHandle(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        self.loop.run_until_complete(_loop.shutdown_default_executor())
        self.loop.check_resouces(strict=True)
        self.loop.close()

    def test_call_soon(self):
        async def f():
            fut = self.loop.create_future()
            handle = self.loop.call_soon(fut.set_result, None)
            res = await fut
            self.assertFalse(handle.cancelled())
            self.assertIsNone(res)

        self.loop.run_until_complete(f())

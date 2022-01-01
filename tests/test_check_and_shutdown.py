import asyncio
import re
import signal
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


class TestCheckAndShutdown(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_tasks(self):
        async def g():
            await asyncio.sleep(100)

        async def f():
            task = asyncio.create_task(g())
            with self.assertWarnsRegex(
                ResourceWarning, re.escape(f"Unfinished task {task!r}")
            ):
                await self.loop.check_and_shutdown()

            self.assertTrue(task.cancelled())

        self.loop.run_until_complete(f())

    def test_signals(self):
        async def f():
            self.loop.add_signal_handler(signal.SIGINT, lambda: None)
            with self.assertWarnsRegex(ResourceWarning, "Unregistered signal"):
                await self.loop.check_and_shutdown()

        self.loop.run_until_complete(f())

    def test_server(self):
        async def f():
            async def serve(reader, writer):
                pass

            server = await asyncio.start_server(serve, host="127.0.0.1", port=0)
            with self.assertWarnsRegex(
                ResourceWarning, re.escape(f"Unclosed server {server!r}")
            ):
                await self.loop.check_and_shutdown()

            self.assertFalse(server.is_serving())

        self.loop.run_until_complete(f())


if __name__ == "__main__":
    unittest.main()

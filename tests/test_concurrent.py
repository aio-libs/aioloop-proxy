import asyncio
import unittest

import aioloop_proxy

_loop = None


def setUpModule():
    global _loop
    _loop = asyncio.new_event_loop()


def tearDownModule():
    global _loop
    if hasattr(_loop, "shutdown_default_executor"):
        _loop.run_until_complete(_loop.shutdown_default_executor())
    _loop.run_until_complete(_loop.shutdown_asyncgens())
    _loop.close()
    _loop = None


class TestConcurrent(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_concurrent_client_server(self):
        async def serve(reader, writer):
            # served by outer loop
            while True:
                data = await reader.read(1024)
                if not data:
                    return
                writer.write(b"ACK:" + data)

        async def client(addr):
            # served by inner loop
            reader, writer = await asyncio.open_connection(*addr)
            writer.write(b"DATA\n")
            data = await reader.read(1024)
            self.assertEqual(data, b"ACK:DATA\n")
            writer.close()
            await writer.wait_closed()
            return "done"

        server = self.loop.run_until_complete(
            asyncio.start_server(serve, "127.0.0.1", 0)
        )
        addr = server.sockets[0].getsockname()

        with aioloop_proxy.proxy(self.loop) as proxy:
            ret = proxy.run_until_complete(client(addr))
            self.assertEqual("done", ret)

        server.close()
        self.loop.run_until_complete(server.wait_closed())

    def test_call_parent_async_function(self):
        class Client:
            # emulates typical async client
            # that holds the current loop instance

            def __init__(self):
                self._loop = asyncio.get_running_loop()

            async def call(self, value):
                fut = self._loop.create_future()
                self._loop.call_soon(fut.set_result, value)
                return await fut

        async def create_client():
            return Client()

        async def go(client):
            ret = await client.call(1)
            self.assertEqual(ret, 1)
            return "done"

        client = self.loop.run_until_complete(create_client())

        with aioloop_proxy.proxy(self.loop) as proxy:
            ret = proxy.run_until_complete(go(client))
            self.assertEqual("done", ret)


if __name__ == "__main__":
    unittest.main()

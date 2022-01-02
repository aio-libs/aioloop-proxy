import asyncio
import os
import sys
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


@unittest.skipIf(sys.platform == "win32", "Not supported by Windows")
class TestTransport(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_add_reader(self):
        async def f():
            rpipe, wpipe = os.pipe()
            os.set_blocking(rpipe, False)
            fut = self.loop.create_future()

            def on_read():
                data = os.read(rpipe, 1024)
                fut.set_result(data)

            self.loop.add_reader(rpipe, on_read)
            os.write(wpipe, b"MSG")

            ret = await fut
            self.assertEqual(ret, b"MSG")

            self.loop.remove_reader(rpipe)
            os.close(rpipe)
            os.close(wpipe)

        self.loop.run_until_complete(f())

    def test_remove_reader(self):
        async def f():
            rpipe, wpipe = os.pipe()
            os.set_blocking(rpipe, False)

            self.assertFalse(self.loop.remove_reader(rpipe))

            self.loop.add_reader(rpipe, lambda: None)
            self.assertTrue(self.loop.remove_reader(rpipe))

            self.assertFalse(self.loop.remove_reader(rpipe))

            os.close(rpipe)
            os.close(wpipe)

        self.loop.run_until_complete(f())

    def test_remove_reader_closed_loop(self):
        rpipe, wpipe = os.pipe()
        os.set_blocking(rpipe, False)

        self.loop.close()
        self.assertFalse(self.loop.remove_reader(rpipe))

        os.close(rpipe)
        os.close(wpipe)

    def test_add_writer(self):
        async def f():
            rpipe, wpipe = os.pipe()
            os.set_blocking(wpipe, False)
            fut = self.loop.create_future()

            def on_write():
                os.write(wpipe, b"MSG")
                fut.set_result(None)
                self.loop.remove_writer(wpipe)

            # Send very large data to overflow write buffer,
            sent = os.write(wpipe, b"0123456789ABCDEF" * 1024 * 1024)
            # Add pending writer
            self.loop.add_writer(wpipe, on_write)

            # Read sent buffer to unblock registered writeer
            data = os.read(rpipe, sent)
            while len(data) < sent:
                sent -= len(data)
                data = os.read(rpipe, sent)

            # wait and check
            await fut
            data = os.read(rpipe, 1024)
            self.assertEqual(data, b"MSG")
            os.close(rpipe)

        self.loop.run_until_complete(f())

    def test_remove_writer(self):
        async def f():
            rpipe, wpipe = os.pipe()
            os.set_blocking(wpipe, False)

            self.assertFalse(self.loop.remove_writer(wpipe))

            self.loop.add_writer(wpipe, lambda: None)
            self.assertTrue(self.loop.remove_writer(wpipe))

            self.assertFalse(self.loop.remove_writer(wpipe))

            os.close(rpipe)
            os.close(wpipe)

        self.loop.run_until_complete(f())

    def test_remove_writer_closed_loop(self):
        rpipe, wpipe = os.pipe()
        os.set_blocking(wpipe, False)

        self.loop.close()
        self.assertFalse(self.loop.remove_writer(wpipe))

        os.close(rpipe)
        os.close(wpipe)


if __name__ == "__main__":
    unittest.main()

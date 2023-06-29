import asyncio
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


class TestHandleNonDebug(unittest.TestCase):
    def setUp(self) -> None:
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)
        self.loop.set_debug(False)

    def tearDown(self) -> None:
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_call_soon(self) -> None:
        async def f() -> None:
            fut = self.loop.create_future()
            handle = self.loop.call_soon(fut.set_result, None)
            self.assertFalse(handle.cancelled())
            res = await fut
            self.assertFalse(handle.cancelled())
            self.assertIsNone(res)

        self.loop.run_until_complete(f())

    def test_call_soon_threadsafe(self) -> None:
        async def f() -> None:
            fut = self.loop.create_future()
            handle = self.loop.call_soon_threadsafe(fut.set_result, None)
            self.assertFalse(handle.cancelled())
            res = await fut
            self.assertFalse(handle.cancelled())
            self.assertIsNone(res)

        self.loop.run_until_complete(f())

    def test_call_soon_check_loop(self) -> None:
        async def f() -> None:
            called = False

            def cb() -> None:
                nonlocal called
                self.assertIs(asyncio.get_running_loop(), self.loop)
                called = True

            handle = self.loop.call_soon(cb)
            self.assertFalse(handle.cancelled())
            await asyncio.sleep(0)
            self.assertFalse(handle.cancelled())
            self.assertTrue(called)

        self.loop.run_until_complete(f())

    def test_cancel(self) -> None:
        async def f() -> None:
            called = False

            def cb() -> None:
                nonlocal called
                called = True

            handle = self.loop.call_soon(cb)
            handle.cancel()
            await asyncio.sleep(0)
            self.assertTrue(handle.cancelled())
            self.assertFalse(called)

        self.loop.run_until_complete(f())

    def test_cancel_after_execution(self) -> None:
        async def f() -> None:
            called = False

            def cb() -> None:
                nonlocal called
                called = True

            handle = self.loop.call_soon(cb)
            await asyncio.sleep(0)
            self.assertFalse(handle.cancelled())
            handle.cancel()
            self.assertTrue(handle.cancelled())
            self.assertTrue(called)

        self.loop.run_until_complete(f())

    def test_cancel_parent(self) -> None:
        async def f() -> None:
            called = False

            def cb() -> None:
                nonlocal called
                called = True

            handle = self.loop.call_soon(cb)
            handle._parent.cancel()  # type: ignore[attr-defined]
            self.assertTrue(handle.cancelled())
            self.assertFalse(called)

        self.loop.run_until_complete(f())

    def test_cancel_twice(self) -> None:
        async def f() -> None:
            called = False

            def cb() -> None:
                nonlocal called
                called = True

            handle = self.loop.call_soon(cb)
            handle.cancel()
            self.assertTrue(handle.cancelled())
            handle.cancel()
            self.assertTrue(handle.cancelled())
            self.assertFalse(called)

        self.loop.run_until_complete(f())

    def test_keep_internal_reference(self) -> None:
        async def f() -> None:
            fut = self.loop.create_future()
            self.loop.call_soon(fut.set_result, None)
            res = await fut
            self.assertIsNone(res)

        self.loop.run_until_complete(f())


class TestHandleDebug(TestHandleNonDebug):
    def setUp(self) -> None:
        super().setUp()
        self.loop.set_debug(True)


class TestTimeHandleNonDebug(unittest.TestCase):
    def setUp(self) -> None:
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)
        self.loop.set_debug(False)

    def tearDown(self) -> None:
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_call_later(self) -> None:
        async def f() -> None:
            fut = self.loop.create_future()
            handle = self.loop.call_later(0.1, fut.set_result, None)
            self.assertFalse(handle.cancelled())
            t0 = self.loop.time()
            res = await fut
            t1 = self.loop.time()
            self.assertFalse(handle.cancelled())
            self.assertIsNone(res)
            self.assertLess(t0, t1)
            self.assertGreater(t1 - t0, 0.07)

        self.loop.run_until_complete(f())

    def test_call_later_check_loop(self) -> None:
        async def f() -> None:
            fut = self.loop.create_future()

            def cb() -> None:
                self.assertIs(asyncio.get_running_loop(), self.loop)
                fut.set_result(None)

            handle = self.loop.call_later(0.1, cb)
            ret = await fut
            self.assertFalse(handle.cancelled())
            self.assertIsNone(ret)

        self.loop.run_until_complete(f())

    def test_call_later_cancel(self) -> None:
        async def f() -> None:
            called = False

            def cb() -> None:
                nonlocal called
                called = True

            handle = self.loop.call_later(0.001, cb)
            handle.cancel()
            await asyncio.sleep(0.1)
            self.assertTrue(handle.cancelled())
            self.assertFalse(called)

        self.loop.run_until_complete(f())

    def test_call_at(self) -> None:
        async def f() -> None:
            fut = self.loop.create_future()
            t0 = self.loop.time()
            handle = self.loop.call_at(t0 + 0.1, fut.set_result, None)
            res = await fut
            t1 = self.loop.time()
            self.assertFalse(handle.cancelled())
            self.assertIsNone(res)
            self.assertLess(t0, t1)
            self.assertGreater(t1 - t0, 0.07)

        self.loop.run_until_complete(f())

    def test_call_at_check_loop(self) -> None:
        async def f() -> None:
            fut = self.loop.create_future()

            def cb() -> None:
                self.assertIs(asyncio.get_running_loop(), self.loop)
                fut.set_result(None)

            handle = self.loop.call_at(self.loop.time() + 0.1, cb)
            ret = await fut
            self.assertFalse(handle.cancelled())
            self.assertIsNone(ret)

        self.loop.run_until_complete(f())

    def test_call_at_cancel(self) -> None:
        async def f() -> None:
            called = False

            def cb() -> None:
                nonlocal called
                called = True

            handle = self.loop.call_at(self.loop.time() + 0.001, cb)
            handle.cancel()
            await asyncio.sleep(0.1)
            self.assertTrue(handle.cancelled())
            self.assertFalse(called)

        self.loop.run_until_complete(f())


class TestTimeHandleDebug(TestTimeHandleNonDebug):
    def setUp(self) -> None:
        super().setUp()
        self.loop.set_debug(True)

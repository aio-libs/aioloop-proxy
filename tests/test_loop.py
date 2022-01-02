import asyncio
import os
import signal
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

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


class TestLoop(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_repr(self):
        debug = self.loop.get_debug()
        with aioloop_proxy.proxy(self.loop) as proxy:
            self.assertEqual(
                repr(proxy),
                f"<LoopProxy running=False closed=False debug={debug}>",
            )

    def test_slow_callback_duration(self):
        value = self.loop.slow_callback_duration
        with aioloop_proxy.proxy(self.loop) as proxy:
            self.assertEqual(proxy.slow_callback_duration, value)
            proxy.slow_callback_duration = 1
            self.assertEqual(proxy.slow_callback_duration, 1)
            self.assertEqual(self.loop.slow_callback_duration, 1)
        self.assertEqual(self.loop.slow_callback_duration, value)

    def test_debug(self):
        value = self.loop.get_debug()
        with aioloop_proxy.proxy(self.loop) as proxy:
            self.assertEqual(proxy.get_debug(), value)
            # negation allows the test pass in both normal and '-X dev' modes
            proxy.set_debug(not value)
            self.assertEqual(proxy.get_debug(), not value)
            self.assertEqual(self.loop.get_debug(), not value)
        self.assertEqual(self.loop.get_debug(), value)

    def test_exception_handler(self):
        self.assertIsNone(self.loop.get_exception_handler())
        with aioloop_proxy.proxy(self.loop) as proxy:
            self.assertIsNone(proxy.get_exception_handler())

            def f(loop, ctx):
                pass

            proxy.set_exception_handler(f)
            self.assertIs(proxy.get_exception_handler(), f)
            self.assertIs(self.loop.get_exception_handler(), f)

        self.assertIsNone(self.loop.get_exception_handler())

    def test_default_exception_handler(self):
        with mock.patch.object(self.loop, "default_exception_handler") as deh:
            with aioloop_proxy.proxy(self.loop) as proxy:
                ctx = {"a": "b"}
                proxy.default_exception_handler(ctx)
                deh.assert_called_once_with(ctx)

        self.assertIsNone(self.loop.get_exception_handler())

    def test_call_exception_handler(self):
        with aioloop_proxy.proxy(self.loop) as proxy:
            handler = mock.Mock()
            proxy.set_exception_handler(handler)
            ctx = {"a": "b"}
            proxy.call_exception_handler(ctx)
            # N.B. 'loop' argument is the top-level loop
            handler.assert_called_once_with(_loop, ctx)

    def test_task_factory(self):
        self.assertIsNone(self.loop.get_task_factory())
        with aioloop_proxy.proxy(self.loop) as proxy:
            called = False

            def factory(loop, coro):
                nonlocal called
                called = True
                return asyncio.Task(coro, loop=loop)

            proxy.set_task_factory(factory)
            # parent is not touched
            self.assertIsNone(self.loop.get_task_factory())

            async def f():
                await asyncio.sleep(0)
                return "done"

            async def g():
                self.assertIs(asyncio.get_running_loop(), proxy)
                task = proxy.create_task(f(), name="named-task")
                if sys.version_info >= (3, 9):
                    self.assertEqual(task.get_name(), "named-task")
                ret = await task
                return ret

            ret = proxy.run_until_complete(g())
            self.assertEqual(ret, "done")
            self.assertTrue(called)

            proxy.set_task_factory(None)
            self.assertIsNone(proxy.get_task_factory())

    def test_task_factory_invalid(self):
        with aioloop_proxy.proxy(self.loop) as proxy:
            with self.assertRaisesRegex(
                TypeError, "task factory must be a callable or None"
            ):
                proxy.set_task_factory(123)

    def test_run_forever(self):
        called = False

        def cb():
            nonlocal called
            self.assertIs(asyncio.get_running_loop(), self.loop)
            called = True
            self.loop.stop()

        self.loop.call_soon(cb)
        self.loop.run_forever()
        self.assertTrue(called)

    def test_run_until_complete_fut(self):
        fut = self.loop.create_future()
        fut.set_result("done")
        ret = self.loop.run_until_complete(fut)
        self.assertEqual(ret, "done")

    def test_run_custom_executor(self):
        async def f():
            def g():
                return "done"

            with ThreadPoolExecutor() as pool:
                ret = await self.loop.run_in_executor(pool, g)
            self.assertEqual(ret, "done")

        self.loop.run_until_complete(f())

    def test_run_custom_default_executor(self):
        async def f():
            def g():
                return "done"

            ret = await self.loop.run_in_executor(None, g)
            self.assertEqual(ret, "done")

        pool = ThreadPoolExecutor()
        self.loop.set_default_executor(pool)
        self.assertIs(pool, self.loop._default_executor)
        self.loop.run_until_complete(f())

    def test_invalid_custom_default_executor(self):
        with self.assertRaisesRegex(
            TypeError, "executor must be ThreadPoolExecutor instance"
        ):
            self.loop.set_default_executor(123)

    def test_close_custom_default_executor_with_warning(self):
        async def f():
            def g():
                return "done"

            self.loop.set_default_executor(ThreadPoolExecutor())
            ret = await self.loop.run_in_executor(None, g)
            self.assertEqual(ret, "done")

        self.loop.run_until_complete(f())
        with self.assertWarnsRegex(
            RuntimeWarning,
            r"Please call 'await proxy\.shutdown_default_executor\(\) explicitly",
        ):
            self.loop.close()

    def test_close_running_event_loop(self):
        async def f():
            with self.assertRaisesRegex(
                RuntimeError, "Cannot close a running event loop"
            ):
                self.loop.close()

        self.loop.run_until_complete(f())

    def test_shutdown_default_executor_fails(self):
        async def f():
            executor = mock.Mock(spec=ThreadPoolExecutor)
            executor.shutdown.side_effect = RuntimeError("Shutdown failed")
            self.loop.set_default_executor(executor)

            with self.assertRaisesRegex(RuntimeError, "Shutdown failed"):
                await self.loop.shutdown_default_executor()

        self.loop.run_until_complete(f())

    def test_default_executor_call_after_shutdown(self):
        async def f():
            called = False

            def g():
                nonlocal called
                called = True

            await self.loop.shutdown_default_executor()
            with self.assertRaisesRegex(
                RuntimeError, "Executor shutdown has been called"
            ):
                await self.loop.run_in_executor(None, g)

        self.loop.run_until_complete(f())

    def test_check_closed(self):
        self.loop.close()
        with self.assertRaisesRegex(RuntimeError, "Event loop is closed"):
            self.loop.call_soon(lambda: None)

    def test_shutdown_asyncgens(self):
        async def f():
            with self.assertWarnsRegex(
                RuntimeWarning, "Only the original loop can shutdown async generators"
            ):
                await self.loop.shutdown_asyncgens()

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows has no UNIX signals")
    def test_signal_handler(self):
        async def f():
            self.assertFalse(self.loop.remove_signal_handler(signal.SIGINT))
            called = False

            def cb():
                nonlocal called
                called = True

            self.loop.add_signal_handler(signal.SIGINT, cb)
            os.kill(os.getpid(), signal.SIGINT)
            await asyncio.sleep(0.01)  # served by outer loop

            self.assertTrue(called)
            self.assertTrue(self.loop.remove_signal_handler(signal.SIGINT))

        self.loop.run_until_complete(f())

    def test_chain_future_cancel_targt(self):
        async def f():
            src = self.loop.create_future()
            tgt = self.loop.create_future()

            self.loop._chain_future(tgt, src)
            tgt.cancel()
            await asyncio.sleep(0)
            self.assertTrue(src.cancelled())

        self.loop.run_until_complete(f())

    def test_chain_future_cancel_source(self):
        async def f():
            src = self.loop.create_future()
            tgt = self.loop.create_future()

            self.loop._chain_future(tgt, src)
            src.cancel()
            await asyncio.sleep(0)
            self.assertTrue(tgt.cancelled())

        self.loop.run_until_complete(f())

    def test_chain_future_source_exception(self):
        async def f():
            src = self.loop.create_future()
            tgt = self.loop.create_future()

            self.loop._chain_future(tgt, src)
            src.set_exception(RuntimeError())
            await asyncio.sleep(0)
            self.assertFalse(tgt.cancelled())
            with self.assertRaises(RuntimeError):
                await tgt

        self.loop.run_until_complete(f())

    def test_advance_time(self):
        async def f():
            fut = self.loop.create_future()

            self.loop.call_later(24 * 3600, fut.set_result, None)
            self.assertFalse(fut.done())

            self.loop.advance_time(24 * 3600)
            self.assertFalse(fut.done())
            await asyncio.sleep(0.01)
            self.assertTrue(fut.done())

        self.loop.run_until_complete(f())

    def test_advance_time_cancelled(self):
        async def f():
            fut = self.loop.create_future()

            timer = self.loop.call_later(24 * 3600, fut.set_result, None)
            self.assertFalse(fut.done())
            timer._parent.cancel()

            self.loop.advance_time(24 * 3600)
            self.assertFalse(fut.done())
            await asyncio.sleep(0.01)
            self.assertFalse(fut.done())

        self.loop.run_until_complete(f())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import contextvars
import gc
import io
import random
import sys
import traceback
import unittest
from types import GenericAlias
from typing import Any, Generator
from unittest import mock

import aioloop_proxy
from aioloop_proxy._task import Future, Task

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


async def coroutine_function() -> None:
    pass


def get_innermost_context(
    exc: BaseException,
) -> tuple[type[BaseException], tuple[Any, ...], int]:
    # Return information about the innermost exception context in the chain.
    depth = 0
    while True:
        context = exc.__context__
        if context is None:
            break

        exc = context
        depth += 1

    return (type(exc), exc.args, depth)


class Dummy:
    def __repr__(self) -> str:
        return "<Dummy>"

    def __call__(self, *args: Any) -> None:
        pass


class CoroLikeObject:
    def send(self, v: Any) -> None:
        raise StopIteration(42)

    def throw(self, *exc: Any) -> None:
        pass

    def close(self) -> None:
        pass

    def __await__(self) -> Generator[Any, None, Any]:
        return self  # type: ignore[return-value]


class TaskTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def test_generic_alias(self) -> None:
        task = Task[str]
        self.assertEqual(task.__args__, (str,))  # type: ignore[attr-defined]
        self.assertIsInstance(task, GenericAlias)

    def test_task_cancel_message_getter(self) -> None:
        async def coro() -> None:
            pass

        t = Task(coro(), loop=self.loop)
        self.assertTrue(hasattr(t, "_cancel_message"))
        self.assertEqual(t._cancel_message, None)

        t.cancel("my message")
        self.assertEqual(t._cancel_message, "my message")

        with self.assertRaises(asyncio.CancelledError) as cm:
            self.loop.run_until_complete(t)

        self.assertEqual("my message", cm.exception.args[0])

    def test_task_cancel_message_setter(self) -> None:
        async def coro() -> None:
            pass

        t = Task(coro(), loop=self.loop)
        t.cancel("my message")
        t._cancel_message = "my new message"
        self.assertEqual(t._cancel_message, "my new message")

        with self.assertRaises(asyncio.CancelledError) as cm:
            self.loop.run_until_complete(t)

        self.assertEqual("my new message", cm.exception.args[0])

    def test_task_del_collect(self) -> None:
        class Evil:
            def __del__(self) -> None:
                gc.collect()

        async def run() -> Evil:
            return Evil()

        self.loop.run_until_complete(
            asyncio.gather(*[Task(run(), loop=self.loop) for _ in range(100)])
        )

    def test_other_loop_future(self) -> None:
        other_loop = asyncio.new_event_loop()
        fut: Future[Any] = Future(loop=other_loop)

        async def run(fut: Future[Any]) -> None:
            await fut

        try:
            with self.assertRaisesRegex(
                RuntimeError, r"Task .* got Future .* attached"
            ):
                self.loop.run_until_complete(run(fut))
        finally:
            other_loop.close()

    def test_task_awaits_on_itself(self) -> None:
        async def test() -> None:
            await task

        task = asyncio.ensure_future(test(), loop=self.loop)

        with self.assertRaisesRegex(RuntimeError, "Task cannot await on itself"):
            self.loop.run_until_complete(task)

    def test_task_class(self) -> None:
        async def notmuch() -> str:
            return "ok"

        t = Task(notmuch(), loop=self.loop)
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), "ok")
        self.assertIs(t._loop, self.loop)
        self.assertIs(t.get_loop(), self.loop)

        loop = asyncio.new_event_loop()
        t = Task(notmuch(), loop=loop)
        self.assertIs(t._loop, loop)
        loop.run_until_complete(t)
        loop.close()

    def test_get_stack(self) -> None:
        T = None

        async def foo() -> None:
            await bar()

        async def bar() -> None:
            # test get_stack()
            assert T is not None
            f = T.get_stack(limit=1)
            try:
                self.assertEqual(f[0].f_code.co_name, "foo")
            finally:
                f = None

            # test print_stack()
            file = io.StringIO()
            T.print_stack(limit=1, file=file)
            file.seek(0)
            tb = file.read()
            self.assertRegex(tb, r"foo\(\) running")

        async def runner() -> None:
            nonlocal T
            T = asyncio.ensure_future(foo(), loop=self.loop)
            await T

        self.loop.run_until_complete(runner())

    def test_task_set_name_pylong(self) -> None:
        # test that setting the task name to a PyLong explicitly doesn't
        # incorrectly trigger the deferred name formatting logic
        async def notmuch() -> int:
            return 123

        t = Task(notmuch(), loop=self.loop, name=987654321)  # type: ignore[arg-type]
        self.assertEqual(t.get_name(), "987654321")
        t.set_name(123456789)  # type: ignore[arg-type]
        self.assertEqual(t.get_name(), "123456789")
        self.loop.run_until_complete(t)

    def test_task_repr_name_not_str(self) -> None:
        async def notmuch() -> int:
            return 123

        t = Task(notmuch(), loop=self.loop)
        t.set_name({6})  # type: ignore[arg-type]
        self.assertEqual(t.get_name(), "{6}")
        self.loop.run_until_complete(t)

    def test_task_basics(self) -> None:
        async def outer() -> int:
            a = await inner1()
            b = await inner2()
            return a + b

        async def inner1() -> int:
            return 42

        async def inner2() -> int:
            return 1000

        t = outer()
        self.assertEqual(self.loop.run_until_complete(t), 1042)

    def test_exception_chaining_after_await(self) -> None:
        # Test that when awaiting on a task when an exception is already
        # active, if the task raises an exception it will be chained
        # with the original.
        async def raise_error() -> None:
            raise ValueError

        async def run() -> None:
            try:
                raise KeyError(3)
            except Exception:
                task = Task(raise_error(), loop=self.loop)
                try:
                    await task
                except Exception as exc:
                    self.assertEqual(type(exc), ValueError)
                    chained = exc.__context__
                    assert chained is not None
                    self.assertEqual((type(chained), chained.args), (KeyError, (3,)))

        task = Task(run(), loop=self.loop)
        self.loop.run_until_complete(task)

    def test_exception_chaining_after_await_with_context_cycle(self) -> None:
        # Check trying to create an exception context cycle:
        # https://bugs.python.org/issue40696
        has_cycle = None

        async def process_exc(exc: BaseException) -> None:
            raise exc

        async def run() -> None:
            nonlocal has_cycle
            try:
                raise KeyError("a")
            except Exception as exc:
                task = Task(process_exc(exc), loop=self.loop)
                try:
                    await task
                except BaseException as exc:
                    has_cycle = exc is exc.__context__
                    # Prevent a hang if has_cycle is True.
                    exc.__context__ = None

        task = Task(run(), loop=self.loop)
        self.loop.run_until_complete(task)
        # This also distinguishes from the initial has_cycle=None.
        self.assertEqual(has_cycle, False)

    def test_cancelling(self) -> None:
        async def task() -> None:
            await asyncio.sleep(10)

        t = Task(task(), loop=self.loop)
        self.assertFalse(t.cancelling())
        self.assertNotIn(" cancelling ", repr(t))
        self.assertTrue(t.cancel())
        self.assertTrue(t.cancelling())
        self.assertIn(" cancelling ", repr(t))

        # Since we commented out two lines from Task.cancel(),
        # this t.cancel() call now returns True.
        # self.assertFalse(t.cancel())
        self.assertTrue(t.cancel())

        with self.assertRaises(asyncio.CancelledError):
            self.loop.run_until_complete(t)

    def test_uncancel_basic(self) -> None:
        async def task() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                t = asyncio.current_task()
                assert t is not None
                t.uncancel()
                await asyncio.sleep(10)

        t = Task(task(), loop=self.loop)
        self.loop.run_until_complete(asyncio.sleep(0.01))

        # Cancel first sleep
        self.assertTrue(t.cancel())
        self.assertIn(" cancelling ", repr(t))
        self.assertEqual(t.cancelling(), 1)
        self.assertFalse(t.cancelled())  # Task is still not complete
        self.loop.run_until_complete(asyncio.sleep(0.01))

        # after .uncancel()
        self.assertNotIn(" cancelling ", repr(t))
        self.assertEqual(t.cancelling(), 0)
        self.assertFalse(t.cancelled())  # Task is still not complete

        # Cancel second sleep
        self.assertTrue(t.cancel())
        self.assertEqual(t.cancelling(), 1)
        self.assertFalse(t.cancelled())  # Task is still not complete
        with self.assertRaises(asyncio.CancelledError):
            self.loop.run_until_complete(t)
        self.assertTrue(t.cancelled())  # Finally, task complete
        self.assertTrue(t.done())

        # uncancel is no longer effective after the task is complete
        t.uncancel()
        self.assertTrue(t.cancelled())
        self.assertTrue(t.done())

    def test_uncancel_structured_blocks(self) -> None:
        # This test recreates the following high-level structure using uncancel()::
        #
        #     async def make_request_with_timeout():
        #         try:
        #             async with asyncio.timeout(1):
        #                 # Structured block affected by the timeout:
        #                 await make_request()
        #                 await make_another_request()
        #         except TimeoutError:
        #             pass  # There was a timeout
        #         # Outer code not affected by the timeout:
        #         await unrelated_code()

        async def make_request_with_timeout(
            *, sleep: float, timeout: float
        ) -> tuple[bool, bool, bool]:
            task = asyncio.current_task()
            assert task is not None
            loop = task.get_loop()

            timed_out = False
            structured_block_finished = False
            outer_code_reached = False

            def on_timeout() -> None:
                nonlocal timed_out
                timed_out = True
                task.cancel()

            timeout_handle = loop.call_later(timeout, on_timeout)
            try:
                try:
                    # Structured block affected by the timeout
                    await asyncio.sleep(sleep)
                    structured_block_finished = True
                finally:
                    timeout_handle.cancel()
                    if (
                        timed_out
                        and task.uncancel() == 0
                        and type(sys.exception()) is asyncio.CancelledError
                    ):
                        # Note the five rules that are needed here to satisfy proper
                        # uncancellation:
                        #
                        # 1. handle uncancellation in a `finally:` block to allow for
                        #    plain returns;
                        # 2. our `timed_out` flag is set, meaning that it was our event
                        #    that triggered the need to uncancel the task, regardless of
                        #    what exception is raised;
                        # 3. we can call `uncancel()` because *we* called `cancel()`
                        #    before;
                        # 4. we call `uncancel()` but we only continue converting the
                        #    CancelledError to TimeoutError if `uncancel()` caused the
                        #    cancellation request count go down to 0.  We need to look
                        #    at the counter vs having a simple boolean flag because our
                        #    code might have been nested (think multiple timeouts). See
                        #    commit 7fce1063b6e5a366f8504e039a8ccdd6944625cd for
                        #    details.
                        # 5. we only convert CancelledError to TimeoutError; for other
                        #    exceptions raised due to the cancellation (like
                        #    a ConnectionLostError from a database client), simply
                        #    propagate them.
                        #
                        # Those checks need to take place in this exact order to make
                        # sure the `cancelling()` counter always stays in sync.
                        #
                        # Additionally, the original stimulus to `cancel()` the task
                        # needs to be unscheduled to avoid re-cancelling the task later.
                        # Here we do it by cancelling `timeout_handle` in the `finally:`
                        # block.
                        raise TimeoutError
            except TimeoutError:
                self.assertTrue(timed_out)

            # Outer code not affected by the timeout:
            outer_code_reached = True
            await asyncio.sleep(0)
            return timed_out, structured_block_finished, outer_code_reached

        # Test which timed out.
        t1 = Task(make_request_with_timeout(sleep=10.0, timeout=0.1), loop=self.loop)
        timed_out: bool
        structured_block_finished: bool
        outer_code_reached: bool
        (
            timed_out,
            structured_block_finished,
            outer_code_reached,
        ) = self.loop.run_until_complete(t1)
        self.assertTrue(timed_out)
        self.assertFalse(structured_block_finished)  # it was cancelled
        self.assertTrue(outer_code_reached)  # task got uncancelled after leaving
        # the structured block and continued until
        # completion
        self.assertEqual(
            t1.cancelling(), 0
        )  # no pending cancellation of the outer task

        # Test which did not time out.
        t2 = Task(make_request_with_timeout(sleep=0, timeout=10.0), loop=self.loop)
        (
            timed_out,
            structured_block_finished,
            outer_code_reached,
        ) = self.loop.run_until_complete(t2)
        self.assertFalse(timed_out)
        self.assertTrue(structured_block_finished)
        self.assertTrue(outer_code_reached)
        self.assertEqual(t2.cancelling(), 0)

    def test_cancel(self) -> None:
        async def task() -> int:
            await asyncio.sleep(10.0)
            return 12

        t = Task(task(), loop=self.loop)
        self.loop.call_soon(t.cancel)
        with self.assertRaises(asyncio.CancelledError):
            self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertTrue(t.cancelled())
        self.assertFalse(t.cancel())

    def test_cancel_with_message_then_future_result(self) -> None:
        # Test Future.result() after calling cancel() with a message.
        cases = [
            ((), ()),
            ((None,), ()),
            (("my message",), ("my message",)),
            # Non-string values should roundtrip.
            ((5,), (5,)),
        ]
        for cancel_args, expected_args in cases:
            with self.subTest(cancel_args=cancel_args):

                async def sleep() -> None:
                    await asyncio.sleep(10)

                async def coro() -> None:
                    task = Task(sleep(), loop=self.loop)
                    await asyncio.sleep(0)
                    task.cancel(*cancel_args)
                    done, pending = await asyncio.wait([task])
                    task.result()

                task = Task(coro(), loop=self.loop)
                with self.assertRaises(asyncio.CancelledError) as cm:
                    self.loop.run_until_complete(task)
                exc = cm.exception
                self.assertEqual(exc.args, expected_args)

                actual = get_innermost_context(exc)
                self.assertEqual(actual, (asyncio.CancelledError, expected_args, 0))

    def test_cancel_with_message_then_future_exception(self) -> None:
        # Test Future.exception() after calling cancel() with a message.
        cases = [
            ((), ()),
            ((None,), ()),
            (("my message",), ("my message",)),
            # Non-string values should roundtrip.
            ((5,), (5,)),
        ]
        for cancel_args, expected_args in cases:
            with self.subTest(cancel_args=cancel_args):

                async def sleep() -> None:
                    await asyncio.sleep(10)

                async def coro() -> None:
                    task = Task(sleep(), loop=self.loop)
                    await asyncio.sleep(0)
                    task.cancel(*cancel_args)
                    done, pending = await asyncio.wait([task])
                    task.exception()

                task = Task(coro(), loop=self.loop)
                with self.assertRaises(asyncio.CancelledError) as cm:
                    self.loop.run_until_complete(task)
                exc = cm.exception
                self.assertEqual(exc.args, expected_args)

                actual = get_innermost_context(exc)
                self.assertEqual(actual, (asyncio.CancelledError, expected_args, 0))

    def test_cancellation_exception_context(self) -> None:
        fut = self.loop.create_future()

        async def sleep() -> None:
            fut.set_result(None)
            await asyncio.sleep(10)

        async def coro() -> None:
            inner_task = Task(sleep(), loop=self.loop)
            await fut
            self.loop.call_soon(inner_task.cancel, "msg")
            try:
                await inner_task
            except asyncio.CancelledError as ex:
                raise ValueError("cancelled") from ex

        task = Task(coro(), loop=self.loop)
        with self.assertRaises(ValueError) as cm:
            self.loop.run_until_complete(task)
        exc = cm.exception
        self.assertEqual(exc.args, ("cancelled",))

        actual = get_innermost_context(exc)
        self.assertEqual(actual, (asyncio.CancelledError, ("msg",), 1))

    def test_cancel_with_message_before_starting_task(self) -> None:
        async def sleep() -> None:
            await asyncio.sleep(10)

        async def coro() -> None:
            task = Task(sleep(), loop=self.loop)
            # We deliberately leave out the sleep here.
            task.cancel("my message")
            done, pending = await asyncio.wait([task])
            task.exception()

        task = Task(coro(), loop=self.loop)
        with self.assertRaises(asyncio.CancelledError) as cm:
            self.loop.run_until_complete(task)
        exc = cm.exception
        self.assertEqual(exc.args, ("my message",))

        actual = get_innermost_context(exc)
        self.assertEqual(actual, (asyncio.CancelledError, ("my message",), 0))

    def test_cancel_current_task(self) -> None:
        async def task() -> int:
            t.cancel()
            self.assertTrue(t._must_cancel)  # White-box test.
            # The sleep should be cancelled immediately.
            await asyncio.sleep(100)
            return 12

        t = Task(task(), loop=self.loop)
        self.assertFalse(t.cancelled())
        self.assertRaises(asyncio.CancelledError, self.loop.run_until_complete, t)
        self.assertTrue(t.done())
        self.assertTrue(t.cancelled())
        self.assertFalse(t._must_cancel)  # White-box test.
        self.assertFalse(t.cancel())

    def test_cancel_at_end(self) -> None:
        """coroutine end right after task is cancelled"""

        async def task() -> int:
            t.cancel()
            self.assertTrue(t._must_cancel)  # White-box test.
            return 12

        t = Task(task(), loop=self.loop)
        self.assertFalse(t.cancelled())
        self.assertRaises(asyncio.CancelledError, self.loop.run_until_complete, t)
        self.assertTrue(t.done())
        self.assertTrue(t.cancelled())
        self.assertFalse(t._must_cancel)  # White-box test.
        self.assertFalse(t.cancel())

    def test_cancel_awaited_task(self) -> None:
        # This tests for a relatively rare condition when
        # a task cancellation is requested for a task which is not
        # currently blocked, such as a task cancelling itself.
        # In this situation we must ensure that whatever next future
        # or task the cancelled task blocks on is cancelled correctly
        # as well.  See also bpo-34872.
        task: Task[None] | None = None
        nested_task: Task[None] | None = None
        fut: Future[Any] = Future(loop=self.loop)

        async def nested() -> None:
            await fut

        async def coro() -> None:
            nonlocal nested_task
            # Create a sub-task and wait for it to run.
            nested_task = Task(nested(), loop=self.loop)
            await asyncio.sleep(0)

            # Request the current task to be cancelled.
            assert task is not None
            task.cancel()
            # Block on the nested task, which should be immediately
            # cancelled.
            await nested_task

        task = Task(coro(), loop=self.loop)
        with self.assertRaises(asyncio.CancelledError):
            self.loop.run_until_complete(task)

        self.assertTrue(task.cancelled())
        assert nested_task is not None
        self.assertTrue(nested_task.cancelled())
        self.assertTrue(fut.cancelled())

    def test_cancel_traceback_for_future_result(self) -> None:
        # When calling Future.result() on a cancelled task, check that the
        # line of code that was interrupted is included in the traceback.

        async def nested() -> None:
            # This will get cancelled immediately.
            await asyncio.sleep(10)

        async def coro() -> None:
            task = Task(nested(), loop=self.loop)
            await asyncio.sleep(0)
            task.cancel()
            await task  # search target

        task = Task(coro(), loop=self.loop)
        try:
            self.loop.run_until_complete(task)
        except asyncio.CancelledError:
            tb = traceback.format_exc()
            self.assertIn("await asyncio.sleep(10)", tb)
            # The intermediate await should also be included.
            self.assertIn("await task  # search target", tb)
        else:
            self.fail("CancelledError did not occur")

    def test_cancel_traceback_for_future_exception(self) -> None:
        # When calling Future.exception() on a cancelled task, check that the
        # line of code that was interrupted is included in the traceback.

        async def nested() -> None:
            # This will get cancelled immediately.
            await asyncio.sleep(10)

        async def coro() -> None:
            task = Task(nested(), loop=self.loop)
            await asyncio.sleep(0)
            task.cancel()
            done, pending = await asyncio.wait([task])
            task.exception()  # search target

        task = Task(coro(), loop=self.loop)
        try:
            self.loop.run_until_complete(task)
        except asyncio.CancelledError:
            tb = traceback.format_exc()
            self.assertIn("await asyncio.sleep(10)", tb)
            # The intermediate await should also be included.
            self.assertIn("task.exception()  # search target", tb)
        else:
            self.fail("CancelledError did not occur")

    def test_log_traceback(self) -> None:
        async def coro() -> None:
            pass

        task = Task(coro(), loop=self.loop)
        with self.assertRaisesRegex(ValueError, "can only be set to False"):
            task._log_traceback = True
        self.loop.run_until_complete(task)

    def test_task_set_methods(self) -> None:
        async def notmuch() -> str:
            return "ko"

        gen = notmuch()
        task = Task(gen, loop=self.loop)

        with self.assertRaisesRegex(RuntimeError, "not support set_result"):
            task.set_result("ok")

        with self.assertRaisesRegex(RuntimeError, "not support set_exception"):
            task.set_exception(ValueError())

        self.assertEqual(self.loop.run_until_complete(task), "ko")

    def test_current_task(self) -> None:
        self.assertIsNone(asyncio.current_task(loop=self.loop))

        async def coro(loop: asyncio.AbstractEventLoop) -> None:
            self.assertIs(asyncio.current_task(), task)

            self.assertIs(asyncio.current_task(None), task)
            self.assertIs(asyncio.current_task(), task)

        task = Task(coro(self.loop), loop=self.loop)
        self.loop.run_until_complete(task)
        self.assertIsNone(asyncio.current_task(loop=self.loop))

    def test_current_task_with_interleaving_tasks(self) -> None:
        self.assertIsNone(asyncio.current_task(loop=self.loop))

        fut1: Future[Any] = Future(loop=self.loop)
        fut2: Future[Any] = Future(loop=self.loop)

        async def coro1(loop: asyncio.AbstractEventLoop) -> None:
            self.assertIs(asyncio.current_task(), task1)
            await fut1
            self.assertIs(asyncio.current_task(), task1)
            fut2.set_result(True)

        async def coro2(loop: asyncio.AbstractEventLoop) -> None:
            self.assertIs(asyncio.current_task(), task2)
            fut1.set_result(True)
            await fut2
            self.assertIs(asyncio.current_task(), task2)

        task1 = Task(coro1(self.loop), loop=self.loop)
        task2 = Task(coro2(self.loop), loop=self.loop)

        self.loop.run_until_complete(asyncio.wait((task1, task2)))
        self.assertIsNone(asyncio.current_task(loop=self.loop))

    @mock.patch("asyncio.base_events.logger")
    def test_tb_logger_not_called_after_cancel(self, m_log: Any) -> None:
        async def coro() -> None:
            raise TypeError

        async def runner() -> None:
            task = Task(coro(), loop=self.loop)
            await asyncio.sleep(0.05)
            task.cancel()

        self.loop.run_until_complete(runner())
        self.assertFalse(m_log.error.called)

    def test_task_source_traceback(self) -> None:
        self.loop.set_debug(True)

        task = Task(coroutine_function(), loop=self.loop)
        lineno = sys._getframe().f_lineno - 1
        self.assertIsInstance(task._source_traceback, list)
        self.assertEqual(
            task._source_traceback[-2][:3],
            (__file__, lineno, "test_task_source_traceback"),
        )
        self.loop.run_until_complete(task)

    def test_exception_traceback(self) -> None:
        # See http://bugs.python.org/issue28843

        async def foo() -> None:
            1 / 0

        async def main() -> None:
            task = Task(foo(), loop=self.loop)
            await asyncio.sleep(0)  # skip one loop iteration
            exc = task.exception()
            assert exc is not None
            self.assertIsNotNone(exc.__traceback__)

        self.loop.run_until_complete(main())

    def test_create_task_with_noncoroutine(self) -> None:
        with self.assertRaisesRegex(TypeError, "a coroutine was expected, got 123"):
            Task(123, loop=self.loop)  # type: ignore[arg-type]

        # test it for the second time to ensure that caching
        # in asyncio.iscoroutine() doesn't break things.
        with self.assertRaisesRegex(TypeError, "a coroutine was expected, got 123"):
            Task(123, loop=self.loop)  # type: ignore[arg-type]

    def test_create_task_with_async_function(self) -> None:
        async def coro() -> None:
            pass

        task = Task(coro(), loop=self.loop)
        self.assertIsInstance(task, Task)
        self.loop.run_until_complete(task)

        # test it for the second time to ensure that caching
        # in asyncio.iscoroutine() doesn't break things.
        task = Task(coro(), loop=self.loop)
        self.assertIsInstance(task, Task)
        self.loop.run_until_complete(task)

    def test_create_task_with_asynclike_function(self) -> None:
        task = Task(
            CoroLikeObject(),
            loop=self.loop,
        )
        self.assertIsInstance(task, Task)
        self.assertEqual(self.loop.run_until_complete(task), 42)

        # test it for the second time to ensure that caching
        # in asyncio.iscoroutine() doesn't break things.
        task = Task(CoroLikeObject(), loop=self.loop)
        self.assertIsInstance(task, Task)
        self.assertEqual(self.loop.run_until_complete(task), 42)

    def test_bare_create_task(self) -> None:
        async def inner() -> int:
            return 1

        async def coro() -> None:
            task = asyncio.create_task(inner())
            self.assertIsInstance(task, Task)
            ret = await task
            self.assertEqual(1, ret)

        self.loop.run_until_complete(coro())

    def test_bare_create_named_task(self) -> None:
        async def coro_noop() -> None:
            pass

        async def coro() -> None:
            task = asyncio.create_task(coro_noop(), name="No-op")
            self.assertEqual(task.get_name(), "No-op")
            await task

        self.loop.run_until_complete(coro())

    def test_context_1(self) -> None:
        cvar: contextvars.ContextVar[str] = contextvars.ContextVar(
            "cvar", default="nope"
        )

        async def sub() -> None:
            await asyncio.sleep(0.01)
            self.assertEqual(cvar.get(), "nope")
            cvar.set("something else")

        async def main() -> None:
            self.assertEqual(cvar.get(), "nope")
            subtask = Task(sub(), loop=self.loop)
            cvar.set("yes")
            self.assertEqual(cvar.get(), "yes")
            await subtask
            self.assertEqual(cvar.get(), "yes")

        task = Task(main(), loop=self.loop)
        self.loop.run_until_complete(task)

    def test_context_2(self) -> None:
        cvar: contextvars.ContextVar[str] = contextvars.ContextVar(
            "cvar", default="nope"
        )

        async def main() -> None:
            def fut_on_done(fut: Future[str]) -> None:
                # This change must not pollute the context
                # of the "main()" task.
                cvar.set("something else")

            self.assertEqual(cvar.get(), "nope")

            for j in range(2):
                fut: Future[str] = Future(loop=self.loop)
                fut.add_done_callback(fut_on_done)
                cvar.set(f"yes{j}")
                self.loop.call_soon(fut.set_result, None)
                await fut
                self.assertEqual(cvar.get(), f"yes{j}")

                for i in range(3):
                    # Test that task passed its context to add_done_callback:
                    cvar.set(f"yes{i}-{j}")
                    await asyncio.sleep(0.001)
                    self.assertEqual(cvar.get(), f"yes{i}-{j}")

        task = Task(main(), loop=self.loop)
        self.loop.run_until_complete(task)

        self.assertEqual(cvar.get(), "nope")

    def test_context_3(self) -> None:
        # Run 100 Tasks in parallel, each modifying cvar.

        cvar: contextvars.ContextVar[int] = contextvars.ContextVar("cvar", default=-1)

        async def sub(num: int) -> None:
            for i in range(10):
                cvar.set(num + i)
                await asyncio.sleep(random.uniform(0.001, 0.05))
                self.assertEqual(cvar.get(), num + i)

        async def main() -> None:
            tasks = []
            for i in range(100):
                task = self.loop.create_task(sub(random.randint(0, 10)))
                tasks.append(task)

            await asyncio.gather(*tasks)

        self.loop.run_until_complete(main())

        self.assertEqual(cvar.get(), -1)

    def test_context_4(self) -> None:
        cvar: contextvars.ContextVar[int] = contextvars.ContextVar("cvar")

        async def coro(val: int) -> None:
            await asyncio.sleep(0)
            cvar.set(val)

        async def main() -> list[int | None]:
            ret = []
            ctx = contextvars.copy_context()
            ret.append(ctx.get(cvar))
            t1 = Task(coro(1), loop=self.loop, context=ctx)
            await t1
            ret.append(ctx.get(cvar))
            t2 = Task(coro(2), loop=self.loop, context=ctx)
            await t2
            ret.append(ctx.get(cvar))
            return ret

        task = Task(main(), loop=self.loop)
        ret = self.loop.run_until_complete(task)

        self.assertEqual([None, 1, 2], ret)

    def test_context_5(self) -> None:
        cvar: contextvars.ContextVar[int] = contextvars.ContextVar("cvar")

        async def coro(val: int) -> None:
            await asyncio.sleep(0)
            cvar.set(val)

        async def main() -> list[int | None]:
            ret = []
            ctx = contextvars.copy_context()
            ret.append(ctx.get(cvar))
            t1 = asyncio.create_task(coro(1), context=ctx)
            await t1
            ret.append(ctx.get(cvar))
            t2 = asyncio.create_task(coro(2), context=ctx)
            await t2
            ret.append(ctx.get(cvar))
            return ret

        task = Task(main(), loop=self.loop)
        ret = self.loop.run_until_complete(task)

        self.assertEqual([None, 1, 2], ret)

    def test_context_6(self) -> None:
        cvar: contextvars.ContextVar[int] = contextvars.ContextVar("cvar")

        async def coro(val: int) -> None:
            await asyncio.sleep(0)
            cvar.set(val)

        async def main() -> list[int | None]:
            ret = []
            ctx = contextvars.copy_context()
            ret.append(ctx.get(cvar))
            t1 = self.loop.create_task(coro(1), context=ctx)
            await t1
            ret.append(ctx.get(cvar))
            t2 = self.loop.create_task(coro(2), context=ctx)
            await t2
            ret.append(ctx.get(cvar))
            return ret

        task = self.loop.create_task(main())
        ret = self.loop.run_until_complete(task)

        self.assertEqual([None, 1, 2], ret)

    def test_get_coro(self) -> None:
        coro = coroutine_function()
        task = Task(coro, loop=self.loop)
        self.loop.run_until_complete(task)
        self.assertIs(task.get_coro(), coro)

    def test_get_context(self) -> None:
        coro = coroutine_function()
        context = contextvars.copy_context()
        task = Task(coro, loop=self.loop, context=context)
        self.loop.run_until_complete(task)
        self.assertIs(task.get_context(), context)


class SetMethodsTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def test_set_result_causes_invalid_state(self) -> None:
        exc_handler = mock.Mock()
        self.loop.call_exception_handler = exc_handler  # type: ignore[method-assign]

        async def foo() -> int:
            await asyncio.sleep(0.1)
            return 10

        coro = foo()
        task = Task(coro, loop=self.loop)
        Future.set_result(task, "spam")  # type: ignore[misc]

        self.assertEqual(self.loop.run_until_complete(task), "spam")

        exc_handler.assert_called_once()
        exc = exc_handler.call_args[0][0]["exception"]
        with self.assertRaisesRegex(
            asyncio.InvalidStateError, r"step\(\): already done"
        ):
            raise exc

        coro.close()

    def test_set_exception_causes_invalid_state(self) -> None:
        class MyExc(Exception):
            pass

        exc_handler = mock.Mock()
        self.loop.call_exception_handler = exc_handler  # type: ignore[method-assign]

        async def foo() -> int:
            await asyncio.sleep(0.1)
            return 10

        coro = foo()
        task = Task(coro, loop=self.loop)
        Future.set_exception(task, MyExc())

        with self.assertRaises(MyExc):
            self.loop.run_until_complete(task)

        exc_handler.assert_called_once()
        exc = exc_handler.call_args[0][0]["exception"]
        with self.assertRaisesRegex(
            asyncio.InvalidStateError, r"step\(\): already done"
        ):
            raise exc

        coro.close()


class CurrentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def test_current_task_no_running_loop(self) -> None:
        self.assertIsNone(asyncio.current_task(loop=self.loop))

    def test_current_task_no_running_loop_implicit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no running event loop"):
            asyncio.current_task()

    def test_current_task_with_implicit_loop(self) -> None:
        async def coro() -> None:
            self.assertIs(asyncio.current_task(loop=self.loop), task)

            self.assertIs(asyncio.current_task(None), task)
            self.assertIs(asyncio.current_task(), task)

        task = self.loop.create_task(coro())
        self.loop.run_until_complete(task)
        self.assertIsNone(asyncio.current_task(loop=self.loop))


if __name__ == "__main__":
    unittest.main()

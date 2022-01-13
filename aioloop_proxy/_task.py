# Task used by loop proxy
# It is identical to the standard asyncio task with the signle exception:
# a blocking future can belong not to the task's loop only but also
# to any parent loop proxy
# It allows creation, e.g., aiohttp.ClientSession with the parent loop proxy and
# reusing it by children.

import asyncio
import contextvars
import functools
import inspect
import itertools
import linecache
import logging
import reprlib
import sys
import traceback
from _thread import get_ident

_task_name_counter = itertools.count(1).__next__


_PENDING = "PENDING"
_CANCELLED = "CANCELLED"
_FINISHED = "FINISHED"

STACK_DEBUG = logging.DEBUG - 1  # heavy-duty debugging

# Number of stack entries to capture in debug mode.
# The larger the number, the slower the operation in debug mode
# (see extract_stack() in format_helpers.py).
DEBUG_STACK_DEPTH = 10

# base_futures


def _format_callbacks(cb):
    """helper function for Future.__repr__"""
    size = len(cb)
    if not size:
        cb = ""

    def format_cb(callback):
        return _format_callback_source(callback, ())

    if size == 1:
        cb = format_cb(cb[0][0])
    elif size == 2:
        cb = f"{format_cb(cb[0][0])}, {format_cb(cb[1][0])}"
    elif size > 2:
        cb = "{}, <{} more>, {}".format(
            format_cb(cb[0][0]), size - 2, format_cb(cb[-1][0])
        )
    return f"cb=[{cb}]"


# bpo-42183: _repr_running is needed for repr protection
# when a Future or Task result contains itself directly or indirectly.
# The logic is borrowed from @reprlib.recursive_repr decorator.
# Unfortunately, the direct decorator usage is impossible because of
# AttributeError: '_asyncio.Task' object has no attribute '__module__' error.
#
# After fixing this thing we can return to the decorator based approach.
_repr_running = set()


def _future_repr_info(future):
    # (Future) -> str
    """helper function for Future.__repr__"""
    info = [future._state.lower()]
    if future._state == _FINISHED:
        if future._exception is not None:
            info.append(f"exception={future._exception!r}")
        else:
            key = id(future), get_ident()
            if key in _repr_running:
                result = "..."
            else:
                _repr_running.add(key)
                try:
                    # use reprlib to limit the length of the output, especially
                    # for very long strings
                    result = reprlib.repr(future._result)
                finally:
                    _repr_running.discard(key)
            info.append(f"result={result}")
    if future._callbacks:
        info.append(_format_callbacks(future._callbacks))
    if future._source_traceback:
        frame = future._source_traceback[-1]
        info.append(f"created at {frame[0]}:{frame[1]}")
    return info


# base_tasks


def _task_repr_info(task):
    info = _future_repr_info(task)

    if task._must_cancel:
        # replace status
        info[0] = "cancelling"

    info.insert(1, "name=%r" % task.get_name())

    coro = _format_coroutine(task._coro)
    info.insert(2, f"coro=<{coro}>")

    if task._fut_waiter is not None:
        info.insert(3, f"wait_for={task._fut_waiter!r}")
    return info


def _task_get_stack(task, limit):
    frames = []
    if hasattr(task._coro, "cr_frame"):
        # case 1: 'async def' coroutines
        f = task._coro.cr_frame
    elif hasattr(task._coro, "gi_frame"):
        # case 2: legacy coroutines
        f = task._coro.gi_frame
    elif hasattr(task._coro, "ag_frame"):
        # case 3: async generators
        f = task._coro.ag_frame
    else:
        # case 4: unknown objects
        f = None
    if f is not None:
        while f is not None:
            if limit is not None:
                if limit <= 0:
                    break
                limit -= 1
            frames.append(f)
            f = f.f_back
        frames.reverse()
    elif task._exception is not None:
        tb = task._exception.__traceback__
        while tb is not None:
            if limit is not None:
                if limit <= 0:
                    break
                limit -= 1
            frames.append(tb.tb_frame)
            tb = tb.tb_next
    return frames


def _task_print_stack(task, limit, file):
    extracted_list = []
    checked = set()
    for f in task.get_stack(limit=limit):
        lineno = f.f_lineno
        co = f.f_code
        filename = co.co_filename
        name = co.co_name
        if filename not in checked:
            checked.add(filename)
            linecache.checkcache(filename)
        line = linecache.getline(filename, lineno, f.f_globals)
        extracted_list.append((filename, lineno, name, line))

    exc = task._exception
    if not extracted_list:
        print(f"No stack for {task!r}", file=file)
    elif exc is not None:
        print(f"Traceback for {task!r} (most recent call last):", file=file)
    else:
        print(f"Stack for {task!r} (most recent call last):", file=file)

    traceback.print_list(extracted_list, file=file)
    if exc is not None:
        for line in traceback.format_exception_only(exc.__class__, exc):
            print(line, file=file, end="")


# format_helpers


def _get_function_source(func):
    func = inspect.unwrap(func)
    if inspect.isfunction(func):
        code = func.__code__
        return (code.co_filename, code.co_firstlineno)
    if isinstance(func, functools.partial):
        return _get_function_source(func.func)
    if isinstance(func, functools.partialmethod):
        return _get_function_source(func.func)
    return None


def _format_callback_source(func, args):
    func_repr = _format_callback(func, args, None)
    source = _get_function_source(func)
    if source:
        func_repr += f" at {source[0]}:{source[1]}"
    return func_repr


def _format_args_and_kwargs(args, kwargs):
    """Format function arguments and keyword arguments.

    Special case for a single parameter: ('hello',) is formatted as ('hello').
    """
    # use reprlib to limit the length of the output
    items = []
    if args:
        items.extend(reprlib.repr(arg) for arg in args)
    if kwargs:
        items.extend(f"{k}={reprlib.repr(v)}" for k, v in kwargs.items())
    return "({})".format(", ".join(items))


def _format_callback(func, args, kwargs, suffix=""):
    if isinstance(func, functools.partial):
        suffix = _format_args_and_kwargs(args, kwargs) + suffix
        return _format_callback(func.func, func.args, func.keywords, suffix)

    if hasattr(func, "__qualname__") and func.__qualname__:
        func_repr = func.__qualname__
    elif hasattr(func, "__name__") and func.__name__:
        func_repr = func.__name__
    else:
        func_repr = repr(func)

    func_repr += _format_args_and_kwargs(args, kwargs)
    if suffix:
        func_repr += suffix
    return func_repr


def extract_stack(f=None, limit=None):
    # Replacement for traceback.extract_stack() that only does the
    # necessary work for asyncio debug mode.
    if f is None:
        f = sys._getframe().f_back
    if limit is None:
        # Limit the amount of work to a reasonable amount, as extract_stack()
        # can be called for each coroutine and future in debug mode.
        limit = DEBUG_STACK_DEPTH
    stack = traceback.StackSummary.extract(
        traceback.walk_stack(f), limit=limit, lookup_lines=False
    )
    stack.reverse()
    return stack


# coroutines


def _format_coroutine(coro):
    assert asyncio.iscoroutine(coro)

    def get_name(coro):
        # Coroutines compiled with Cython sometimes don't have
        # proper __qualname__ or __name__.  While that is a bug
        # in Cython, asyncio shouldn't crash with an AttributeError
        # in its __repr__ functions.
        if hasattr(coro, "__qualname__") and coro.__qualname__:
            coro_name = coro.__qualname__
        elif hasattr(coro, "__name__") and coro.__name__:
            coro_name = coro.__name__
        else:
            # Stop masking Cython bugs, expose them in a friendly way.
            coro_name = f"<{type(coro).__name__} without __name__>"
        return f"{coro_name}()"

    def is_running(coro):
        try:
            return coro.cr_running
        except AttributeError:
            try:
                return coro.gi_running
            except AttributeError:
                return False

    coro_code = None
    if hasattr(coro, "cr_code") and coro.cr_code:
        coro_code = coro.cr_code
    elif hasattr(coro, "gi_code") and coro.gi_code:
        coro_code = coro.gi_code

    coro_name = get_name(coro)

    if not coro_code:
        # Built-in types might not have __qualname__ or __name__.
        if is_running(coro):
            return f"{coro_name} running"
        else:
            return coro_name

    coro_frame = None
    if hasattr(coro, "gi_frame") and coro.gi_frame:
        coro_frame = coro.gi_frame
    elif hasattr(coro, "cr_frame") and coro.cr_frame:
        coro_frame = coro.cr_frame

    # If Cython's coroutine has a fake code object without proper
    # co_filename -- expose that.
    filename = coro_code.co_filename or "<empty co_filename>"

    lineno = 0

    if coro_frame is not None:
        lineno = coro_frame.f_lineno
        coro_repr = f"{coro_name} running at {filename}:{lineno}"

    else:
        lineno = coro_code.co_firstlineno
        coro_repr = f"{coro_name} done, defined at {filename}:{lineno}"

    return coro_repr


# future


class Future:
    """This class is *almost* compatible with concurrent.futures.Future.

    Differences:

    - This class is not thread-safe.

    - result() and exception() do not take a timeout argument and
      raise an exception when the future isn't done yet.

    - Callbacks registered with add_done_callback() are always called
      via the event loop's call_soon().

    - This class is not compatible with the wait() and as_completed()
      methods in the concurrent.futures package.

    (In Python 3.4 or later we may be able to unify the implementations.)
    """

    # Class variables serving as defaults for instance variables.
    _state = _PENDING
    _result = None
    _exception = None
    _loop = None
    _source_traceback = None
    _cancel_message = None
    # A saved CancelledError for later chaining as an exception context.
    _cancelled_exc = None

    # This field is used for a dual purpose:
    # - Its presence is a marker to declare that a class implements
    #   the Future protocol (i.e. is intended to be duck-type compatible).
    #   The value must also be not-None, to enable a subclass to declare
    #   that it is not compatible by setting this to None.
    # - It is set by __iter__() below so that Task._step() can tell
    #   the difference between
    #   `await Future()` or`yield from Future()` (correct) vs.
    #   `yield Future()` (incorrect).
    _asyncio_future_blocking = False

    __log_traceback = False

    def __init__(self, *, loop=None):
        """Initialize the future.

        The optional event_loop argument allows explicitly setting the event
        loop object used by the future. If it's not provided, the future uses
        the default event loop.
        """
        if loop is None:
            self._loop = asyncio._get_event_loop()
        else:
            self._loop = loop
        self._callbacks = []
        if self._loop.get_debug():
            self._source_traceback = extract_stack(sys._getframe(1))

    _repr_info = _future_repr_info

    def __repr__(self):
        return "<{} {}>".format(self.__class__.__name__, " ".join(self._repr_info()))

    def __del__(self):
        if not self.__log_traceback:
            # set_exception() was not called, or result() or exception()
            # has consumed the exception
            return
        exc = self._exception
        context = {
            "message": f"{self.__class__.__name__} exception was never retrieved",
            "exception": exc,
            "future": self,
        }
        if self._source_traceback:
            context["source_traceback"] = self._source_traceback
        self._loop.call_exception_handler(context)

    def __class_getitem__(cls, type):
        return cls

    @property
    def _log_traceback(self):
        return self.__log_traceback

    @_log_traceback.setter
    def _log_traceback(self, val):
        if val:
            raise ValueError("_log_traceback can only be set to False")
        self.__log_traceback = False

    def get_loop(self):
        """Return the event loop the Future is bound to."""
        loop = self._loop
        if loop is None:
            raise RuntimeError("Future object is not initialized.")
        return loop

    def _make_cancelled_error(self):
        """Create the CancelledError to raise if the Future is cancelled.

        This should only be called once when handling a cancellation since
        it erases the saved context exception value.
        """
        if self._cancel_message is None:
            exc = asyncio.CancelledError()
        else:
            exc = asyncio.CancelledError(self._cancel_message)
        exc.__context__ = self._cancelled_exc
        # Remove the reference since we don't need this anymore.
        self._cancelled_exc = None
        return exc

    def cancel(self, msg=None):
        """Cancel the future and schedule callbacks.

        If the future is already done or cancelled, return False.  Otherwise,
        change the future's state to cancelled, schedule the callbacks and
        return True.
        """
        self.__log_traceback = False
        if self._state != _PENDING:
            return False
        self._state = _CANCELLED
        self._cancel_message = msg
        self.__schedule_callbacks()
        return True

    def __schedule_callbacks(self):
        """Internal: Ask the event loop to call all callbacks.

        The callbacks are scheduled to be called as soon as possible. Also
        clears the callback list.
        """
        callbacks = self._callbacks[:]
        if not callbacks:
            return

        self._callbacks[:] = []
        for callback, ctx in callbacks:
            self._loop.call_soon(callback, self, context=ctx)

    def cancelled(self):
        """Return True if the future was cancelled."""
        return self._state == _CANCELLED

    # Don't implement running(); see http://bugs.python.org/issue18699

    def done(self):
        """Return True if the future is done.

        Done means either that a result / exception are available, or that the
        future was cancelled.
        """
        return self._state != _PENDING

    def result(self):
        """Return the result this future represents.

        If the future has been cancelled, raises CancelledError.  If the
        future's result isn't yet available, raises InvalidStateError.  If
        the future is done and has an exception set, this exception is raised.
        """
        if self._state == _CANCELLED:
            exc = self._make_cancelled_error()
            raise exc
        if self._state != _FINISHED:
            raise asyncio.InvalidStateError("Result is not ready.")
        self.__log_traceback = False
        if self._exception is not None:
            raise self._exception
        return self._result

    def exception(self):
        """Return the exception that was set on this future.

        The exception (or None if no exception was set) is returned only if
        the future is done.  If the future has been cancelled, raises
        CancelledError.  If the future isn't done yet, raises
        InvalidStateError.
        """
        if self._state == _CANCELLED:
            exc = self._make_cancelled_error()
            raise exc
        if self._state != _FINISHED:
            raise asyncio.InvalidStateError("Exception is not set.")
        self.__log_traceback = False
        return self._exception

    def add_done_callback(self, fn, *, context=None):
        """Add a callback to be run when the future becomes done.

        The callback is called with a single argument - the future object. If
        the future is already done when this is called, the callback is
        scheduled with call_soon.
        """
        if self._state != _PENDING:
            self._loop.call_soon(fn, self, context=context)
        else:
            if context is None:
                context = contextvars.copy_context()
            self._callbacks.append((fn, context))

    # New method not in PEP 3148.

    def remove_done_callback(self, fn):
        """Remove all instances of a callback from the "call when done" list.

        Returns the number of callbacks removed.
        """
        filtered_callbacks = [(f, ctx) for (f, ctx) in self._callbacks if f != fn]
        removed_count = len(self._callbacks) - len(filtered_callbacks)
        if removed_count:
            self._callbacks[:] = filtered_callbacks
        return removed_count

    # So-called internal methods (note: no set_running_or_notify_cancel()).

    def set_result(self, result):
        """Mark the future done and set its result.

        If the future is already done when this method is called, raises
        InvalidStateError.
        """
        if self._state != _PENDING:
            raise asyncio.InvalidStateError(f"{self._state}: {self!r}")
        self._result = result
        self._state = _FINISHED
        self.__schedule_callbacks()

    def set_exception(self, exception):
        """Mark the future done and set an exception.

        If the future is already done when this method is called, raises
        InvalidStateError.
        """
        if self._state != _PENDING:
            raise asyncio.InvalidStateError(f"{self._state}: {self!r}")
        if isinstance(exception, type):
            exception = exception()
        if type(exception) is StopIteration:
            raise TypeError(
                "StopIteration interacts badly with generators "
                "and cannot be raised into a Future"
            )
        self._exception = exception
        self._state = _FINISHED
        self.__schedule_callbacks()
        self.__log_traceback = True

    def __await__(self):
        if not self.done():
            self._asyncio_future_blocking = True
            yield self  # This tells Task to wait for completion.
        if not self.done():
            raise RuntimeError("await wasn't used with future")
        return self.result()  # May raise too.

    __iter__ = __await__


class Task(Future):
    """A coroutine wrapped in a Future."""

    # An important invariant maintained while a Task not done:
    #
    # - Either _fut_waiter is None, and _step() is scheduled;
    # - or _fut_waiter is some Future, and _step() is *not* scheduled.
    #
    # The only transition from the latter to the former is through
    # _wakeup().  When _fut_waiter is not None, one of its callbacks
    # must be _wakeup().

    # If False, don't log a message if the task is destroyed whereas its
    # status is still pending
    _log_destroy_pending = True

    def __init__(self, coro, *, loop=None, name=None):
        super().__init__(loop=loop)
        if self._source_traceback:
            del self._source_traceback[-1]
        if not asyncio.iscoroutine(coro):
            # raise after Future.__init__(), attrs are required for __del__
            # prevent logging for pending task in __del__
            self._log_destroy_pending = False
            raise TypeError(f"a coroutine was expected, got {coro!r}")

        if name is None:
            self._name = f"Task-Proxy-{_task_name_counter()}"
        else:
            self._name = str(name)

        self._must_cancel = False
        self._fut_waiter = None
        self._coro = coro
        self._context = contextvars.copy_context()

        self._loop.call_soon(self.__step, context=self._context)
        asyncio._register_task(self)

    def __del__(self):
        if self._state == _PENDING and self._log_destroy_pending:
            context = {
                "task": self,
                "message": "Task was destroyed but it is pending!",
            }
            if self._source_traceback:
                context["source_traceback"] = self._source_traceback
            self._loop.call_exception_handler(context)
        super().__del__()

    def __class_getitem__(cls, type):
        return cls

    def _repr_info(self):
        return _task_repr_info(self)

    def get_coro(self):
        return self._coro

    def get_name(self):
        return self._name

    def set_name(self, value):
        self._name = str(value)

    def set_result(self, result):
        raise RuntimeError("Task does not support set_result operation")

    def set_exception(self, exception):
        raise RuntimeError("Task does not support set_exception operation")

    def get_stack(self, *, limit=None):
        """Return the list of stack frames for this task's coroutine.

        If the coroutine is not done, this returns the stack where it is
        suspended.  If the coroutine has completed successfully or was
        cancelled, this returns an empty list.  If the coroutine was
        terminated by an exception, this returns the list of traceback
        frames.

        The frames are always ordered from oldest to newest.

        The optional limit gives the maximum number of frames to
        return; by default all available frames are returned.  Its
        meaning differs depending on whether a stack or a traceback is
        returned: the newest frames of a stack are returned, but the
        oldest frames of a traceback are returned.  (This matches the
        behavior of the traceback module.)

        For reasons beyond our control, only one stack frame is
        returned for a suspended coroutine.
        """
        return _task_get_stack(self, limit)

    def print_stack(self, *, limit=None, file=None):
        """Print the stack or traceback for this task's coroutine.

        This produces output similar to that of the traceback module,
        for the frames retrieved by get_stack().  The limit argument
        is passed to get_stack().  The file argument is an I/O stream
        to which the output is written; by default output is written
        to sys.stderr.
        """
        return _task_print_stack(self, limit, file)

    def cancel(self, msg=None):
        """Request that this task cancel itself.

        This arranges for a CancelledError to be thrown into the
        wrapped coroutine on the next cycle through the event loop.
        The coroutine then has a chance to clean up or even deny
        the request using try/except/finally.

        Unlike Future.cancel, this does not guarantee that the
        task will be cancelled: the exception might be caught and
        acted upon, delaying cancellation of the task or preventing
        cancellation completely.  The task may also return a value or
        raise a different exception.

        Immediately after this method is called, Task.cancelled() will
        not return True (unless the task was already cancelled).  A
        task will be marked as cancelled when the wrapped coroutine
        terminates with a CancelledError exception (even if cancel()
        was not called).
        """
        self._log_traceback = False
        if self.done():
            return False
        if self._fut_waiter is not None:
            if self._fut_waiter.cancel(msg=msg):
                # Leave self._fut_waiter; it may be a Task that
                # catches and ignores the cancellation so we may have
                # to cancel it again later.
                return True
        # It must be the case that self.__step is already scheduled.
        self._must_cancel = True
        self._cancel_message = msg
        return True

    def __step(self, exc=None):
        if self.done():
            raise asyncio.InvalidStateError(f"_step(): already done: {self!r}, {exc!r}")
        if self._must_cancel:
            if not isinstance(exc, asyncio.CancelledError):
                exc = self._make_cancelled_error()
            self._must_cancel = False
        coro = self._coro
        self._fut_waiter = None

        asyncio._enter_task(self._loop, self)
        # Call either coro.throw(exc) or coro.send(None).
        try:
            if exc is None:
                # We use the `send` method directly, because coroutines
                # don't have `__iter__` and `__next__` methods.
                result = coro.send(None)
            else:
                result = coro.throw(exc)
        except StopIteration as exc:
            if self._must_cancel:
                # Task is cancelled right before coro stops.
                self._must_cancel = False
                super().cancel(msg=self._cancel_message)
            else:
                super().set_result(exc.value)
        except asyncio.CancelledError as exc:
            # Save the original exception so we can chain it later.
            self._cancelled_exc = exc
            super().cancel()  # I.e., Future.cancel(self).
        except (KeyboardInterrupt, SystemExit) as exc:
            super().set_exception(exc)
            raise
        except BaseException as exc:
            super().set_exception(exc)
        else:
            blocking = getattr(result, "_asyncio_future_blocking", None)
            if blocking is not None:
                # Yielded Future must come from Future.__iter__().
                if not self._check_loop(result):
                    new_exc = RuntimeError(
                        f"Task {self!r} got Future "
                        f"{result!r} attached to a different loop"
                    )
                    self._loop.call_soon(self.__step, new_exc, context=self._context)
                elif blocking:
                    if result is self:
                        new_exc = RuntimeError(f"Task cannot await on itself: {self!r}")
                        self._loop.call_soon(
                            self.__step, new_exc, context=self._context
                        )
                    else:
                        result._asyncio_future_blocking = False
                        result.add_done_callback(self.__wakeup, context=self._context)
                        self._fut_waiter = result
                        if self._must_cancel:
                            if self._fut_waiter.cancel(msg=self._cancel_message):
                                self._must_cancel = False
                else:
                    new_exc = RuntimeError(
                        f"yield was used instead of yield from "
                        f"in task {self!r} with {result!r}"
                    )
                    self._loop.call_soon(self.__step, new_exc, context=self._context)

            elif result is None:
                # Bare yield relinquishes control for one event loop iteration.
                self._loop.call_soon(self.__step, context=self._context)
            elif inspect.isgenerator(result):
                # Yielding a generator is just wrong.
                new_exc = RuntimeError(
                    f"yield was used instead of yield from for "
                    f"generator in task {self!r} with {result!r}"
                )
                self._loop.call_soon(self.__step, new_exc, context=self._context)
            else:
                # Yielding something else is an error.
                new_exc = RuntimeError(f"Task got bad yield: {result!r}")
                self._loop.call_soon(self.__step, new_exc, context=self._context)
        finally:
            asyncio._leave_task(self._loop, self)
            self = None  # Needed to break cycles when an exception occurs.

    def __wakeup(self, future):
        try:
            future.result()
        except BaseException as exc:
            # This may also be a cancellation.
            self.__step(exc)
        else:
            # Don't pass the value of `future.result()` explicitly,
            # as `Future.__iter__` and `Future.__await__` don't need it.
            # If we call `_step(value, None)` instead of `_step()`,
            # Python eval loop would use `.send(value)` method call,
            # instead of `__next__()`, which is slower for futures
            # that return non-generator iterators from their `__iter__`.
            self.__step()
        self = None  # Needed to break cycles when an exception occurs.

    def _check_loop(self, fut):
        fut_loop = fut.get_loop()
        loop = self._loop
        while loop is not None:
            if fut_loop is loop:
                return True
            if getattr(loop, "_proxy_loop_marker", False):
                loop = loop._parent
            else:
                loop = None
        return False

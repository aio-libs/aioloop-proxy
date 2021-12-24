import contextlib

from ._loop import _LoopProxy


@contextlib.contextmanager
def proxy(loop):
    proxy_loop = _LoopProxy(loop)
    debug = loop.get_debug()
    exception_handler = loop.get_exception_handler()
    slow_callback_duration = loop.slow_callback_duration
    yield proxy_loop
    loop.set_debug(debug)
    loop.set_exception_handler(exception_handler)
    loop.slow_callback_duration = slow_callback_duration

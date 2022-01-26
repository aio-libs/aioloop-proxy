import asyncio
import contextlib
from typing import Iterator

from ._loop import CheckKind, LoopProxy


@contextlib.contextmanager
def proxy(
    loop: asyncio.AbstractEventLoop, *, kind: CheckKind = CheckKind.ALL
) -> Iterator[LoopProxy]:
    proxy_loop = LoopProxy(loop)
    debug = loop.get_debug()
    exception_handler = loop.get_exception_handler()
    slow_callback_duration = loop.slow_callback_duration
    try:
        yield proxy_loop
    finally:
        if proxy_loop.is_closed():
            return
        if not loop.is_closed():
            proxy_loop.run_until_complete(proxy_loop.shutdown_default_executor())
            proxy_loop.run_until_complete(proxy_loop.check_and_shutdown())
            if loop.get_debug() != debug:
                loop.set_debug(debug)
            if loop.get_exception_handler() != exception_handler:
                loop.set_exception_handler(exception_handler)
            if loop.slow_callback_duration != slow_callback_duration:
                loop.slow_callback_duration = slow_callback_duration
        proxy_loop.close()

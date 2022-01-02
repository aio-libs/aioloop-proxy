aioloop-proxy
=============

A proxy for ``asyncio.AbstractEventLoop`` for testing purposes.

When tests writing for asyncio based code, there are controversial requirements.

First, a signle event loop for the whole test session (or test subset) is desired.  For
example, if web server starts slowly, there is a temptation to create a server only once
and access to the single web server instance from each test.

Second, each test should be isolated.  It means that asyncio tasks (timers, connections,
etc.) created by test A should be finished at test A finalization and should not affect
test B execution.


The library provides *loop proxy* class that fully implements
``asyncio.AbstractEventLoop`` interface but redirects all actual work to the proxied
parent loop.  It allows to check that all activities created with the proxy are finished
on the proxy finalization. In turn, all tasks created with the parent loop are still
keep working during the proxy execution.

Loop proxies can be nested, e.g. global-loop -> module-loop -> test-loop is supported.


The library is test tool agnostic, e.g. it can be integrated with ``unittest`` and
``pytest`` easily (the actual integraion is out of the project scope).

Installation
------------

::

   pip install aioloop-proxy


Usage
-----

::

   import asyncio
   import aioloop_proxy

   loop = asyncio.new_event_loop()
   server_addr = loop.run_until_complete(setup_and_run_test_server())
   ...

   with aioloop_proxy(loop, strict=True) as loop_proxy:
      loop_proxy.run_until_complete(test_func(server_addr))


Sure, each test system (``unittest``, ``pytest``, name it) should not run the code
snippet above as-is but incorporate it as a dedicates test-case class or plugin.


Extra loop methods
------------------

``LoopProxy`` implements all ``asyncio.AbstractEventLoop`` public methods. Additionally,
it provides two proxy-specific ones: ``loop.check_and_shutdown()`` and
``loop.advance_time()``.

``await proxy.check_and_shutdown(kind=CheckKind.ALL)`` can be used for checking if
the proxy finished without active tasks, open transports etc.

``kind`` is a ``enum.Flag`` described as the following::

    class CheckKind(enum.Flag):
        TASKS = enum.auto()
        SIGNALS = enum.auto()
        SERVERS = enum.auto()
        TRANSPORTS = enum.auto()
        READERS = enum.auto()
        WRITERS = enum.auto()
        HANDLES = enum.auto()

        ALL = TASKS | SIGNALS | SERVERS | TRANSPORTS | READERS | WRITERS

All checks are performed by default.  A specific test can omit some check if it raises a
false positive warning.

**N.B.** Dangling resources are always closed even if corresponding ``kind`` is omitted.
A proxy loop should cleanup all acquired resources at the test finish for the sake of
tests isolation principle.


``proxy.advance_time(offset)`` is a perk that helps with writing tests for scenarios
that uses timeouts, delays, etc.

Let's assume, we have a code that should read data from peer or raise ``TimeoutError``
after 15 minute timeout.  It can be done by shifting *the proxy local time*
(``proxy.time()`` returned value) to 15 minutes forward artificially::

    task = asyncio.create_task(fetch_or_timeout())
    loop.advance_time(15 * 60)
    try:
        await task
    except TimeoutError:
        ...

In the example above, ``await task`` is resumed immediatelly because the test
*wall-clock* is shifted by 15 minutes two lines above, and all timers created by the
proxy are adjusted accordingly.

The parent loop wall-clock is not touched.

The method complexity is O(N) where N is amount of active timers created by
``proxy.call_later()`` or ``proxy.call_at()`` methods.

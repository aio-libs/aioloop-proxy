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

Installation::

   pip install aioloop-proxy


The usage is::

   import asyncio
   import aioloop_proxy

   loop = asyncio.new_event_loop()
   ...

   with aioloop_proxy(loop, strict=True) as loop_proxy:
      loop_proxy.run_until_complete(test_func())

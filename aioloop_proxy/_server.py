import asyncio


class _ServerProxy(asyncio.AbstractServer):
    def __init__(self, original, loop):
        self._orig = original
        self._loop = loop

    def __repr__(self):
        return repr(self._orig)

    def get_loop(self):
        return self._loop

    def is_serving(self):
        return self._orig.is_serving()

    @property
    def sockets(self):
        return self._orig.sockets

    def close(self):
        return self._orig.close()

    async def start_serving(self):
        return await self._loop._wrap_async(self._orig.start_serving())

    async def serve_forever(self):
        return await self._loop._wrap_async(self._orig.serve_forever())

    async def wait_closed(self):
        return await self._loop._wrap_async(self._orig.wait_closed())

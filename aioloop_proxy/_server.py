import asyncio


class _ServerProxy(asyncio.AbstractServer):
    def __init__(self, original, loop):
        self._original = original
        self._loop = loop

    def __repr__(self):
        return self._loop._wrap_sync(repr, self._loop)

    def get_loop(self):
        return self._loop

    def is_serving(self):
        return self._loop._wrap_sync(self._original.is_serving)

    @property
    def sockets(self):
        return self._loop._wrap_sync(getattr, self._original, "sockets")

    def close(self):
        return self._loop._wrap_sync(self._original.close)

    async def start_serving(self):
        return await self._loop._wrap_async(self._original.start_serving())

    async def serve_forever(self):
        return await self._loop._wrap_async(self._original.serve_forever())

    async def wait_closed(self):
        return await self._loop._wrap_async(self._original.wait_closed())

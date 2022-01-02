import asyncio


class _ProxyHandleMixin:
    _parent = None

    def _set_parent(self, parent):
        self._parent = parent

    def cancel(self):
        if self.cancelled():
            return
        self._parent.cancel()
        self._discard()
        super().cancel()
        self._parent = None

    def cancelled(self):
        parent = self._parent
        if parent is not None:
            cancelled = parent.cancelled()
            if cancelled:
                self._discard()
                super().cancel()
                self._parent = None
        return super().cancelled()


class _ProxyHandle(_ProxyHandleMixin, asyncio.Handle):
    def _register(self):
        self._loop._ready.add(self)

    def _discard(self):
        self._loop._ready.discard(self)


class _ProxyTimerHandle(_ProxyHandleMixin, asyncio.TimerHandle):
    def _register(self):
        self._loop._timers.add(self)

    def _discard(self):
        self._loop._timers.discard(self)


class _HandleCaller:
    def __init__(self, loop_proxy, handle):
        self.loop_proxy = loop_proxy
        self.handle = handle
        handle._register()

    def __call__(self):
        handle = self.handle
        self.handle = None  # drop circular reference
        handle._discard()
        self.loop_proxy._wrap_cb(handle._run)

import asyncio


class _ProxyHandleMixin:
    _parent = None

    def _set_parent(self, parent):
        self._parent = parent
        self._register()

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

    def _run(self):
        self._discard()
        self._loop._wrap_cb(super()._run)


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

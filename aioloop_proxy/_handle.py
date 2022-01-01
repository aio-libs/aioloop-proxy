import asyncio


class _ProxyHandleMixin:
    _parent = None

    def _set_parent(self, parent):
        self._parent = parent

    def cancel(self):
        if self.cancelled():
            return
        self._parent.cancel()
        self._loop._handles.discard(self)
        super().cancel()
        self._parent = None

    def cancelled(self):
        parent = self._parent
        if parent is not None:
            cancelled = parent.cancelled()
            if cancelled:
                self._loop._handles.discard(self)
                super().cancel()
                self._parent = None
        return super().cancelled()


class _ProxyHandle(_ProxyHandleMixin, asyncio.Handle):
    pass


class _ProxyTimerHandle(_ProxyHandleMixin, asyncio.TimerHandle):
    pass


class _HandleCaller:
    def __init__(self, loop_proxy, handle):
        self.loop_proxy = loop_proxy
        self.handle = handle
        self.loop_proxy._handles.add(handle)

    def __call__(self):
        handle = self.handle
        self.handle = None  # drop circular reference
        self.loop_proxy._handles.discard(handle)
        self.loop_proxy._wrap_cb(handle._run)

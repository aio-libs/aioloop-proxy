import asyncio


class _ProxyHandleMixin:
    _parent = None

    def _set_parent(self, parent):
        self._parent = parent

    def cancel(self):
        self._parent.cancel()

    def cancelled(self):
        return self._parent.cancelled()


class _ProxyHandle(_ProxyHandleMixin, asyncio.Handle):
    pass


class _ProxyTimerHandle(_ProxyHandleMixin, asyncio.TimerHandle):
    pass

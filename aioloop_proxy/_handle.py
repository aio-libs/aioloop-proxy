import asyncio


class _ProxyHandle(asyncio.Handle):
    _parent = None

    def cancel(self):
        if self._parent is not None:
            self._parent.cancel()
        super().cancel()
        self._parent = None

    def cancelled(self):
        if self._parent is not None:
            # Check if parent was cancelled first
            return self._parent.cancelled()
        else:
            return super().cancelled()


class _ProxyTimerHandle(asyncio.TimerHandle):
    _parent = None

    def cancel(self):
        if self._parent is not None:
            self._parent.cancel()
        super().cancel()
        self._parent = None

    def cancelled(self):
        if self._parent is not None:
            return self._parent.cancelled()
        else:
            return super().cancelled()

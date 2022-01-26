import asyncio
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from ._loop import LoopProxy


class _ProxyHandleMixin:
    _parent: Optional[asyncio.Handle] = None
    _loop: "LoopProxy"
    _source_traceback: List[Any]

    def _register(self) -> None:
        raise NotImplementedError

    def _discard(self) -> None:
        raise NotImplementedError

    def _set_parent(self, parent: asyncio.Handle) -> None:
        self._parent = parent
        self._register()

    def cancel(self) -> None:
        if self.cancelled():
            return
        if self._parent is not None:
            self._parent.cancel()
        self._discard()
        super().cancel()  # type: ignore
        self._parent = None

    def cancelled(self) -> bool:
        parent = self._parent
        if parent is not None:
            cancelled = parent.cancelled()
            if cancelled:
                self._discard()
                super().cancel()  # type: ignore
                self._parent = None
        return super().cancelled()  # type: ignore

    def _run(self) -> None:
        self._discard()
        self._loop._wrap_cb(super()._run)  # type: ignore


class _ProxyHandle(_ProxyHandleMixin, asyncio.Handle):
    def _register(self) -> None:
        self._loop._ready.add(self)

    def _discard(self) -> None:
        self._loop._ready.discard(self)


class _ProxyTimerHandle(_ProxyHandleMixin, asyncio.TimerHandle):
    def _register(self) -> None:
        self._loop._timers.add(self)

    def _discard(self) -> None:
        self._loop._timers.discard(self)

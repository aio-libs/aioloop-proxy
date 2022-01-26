import asyncio
from typing import TYPE_CHECKING, Any, Callable, Optional, cast

from ._transport import (
    _BaseTransportProxy,
    _DatagramTransportProxy,
    _make_transport_proxy,
)

if TYPE_CHECKING:
    from ._loop import LoopProxy


class _BaseProtocolProxy(asyncio.BaseProtocol):
    def __init__(self, protocol: asyncio.BaseProtocol, loop: "LoopProxy") -> None:
        self._loop = loop
        self.protocol = protocol
        self.transport: Optional[_BaseTransportProxy] = None
        self.wait_closed = self._loop.create_future()

    def __repr__(self) -> str:
        return repr(self.protocol)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = _make_transport_proxy(transport, self._loop)
        self._loop._wrap_cb(self.protocol.connection_made, self.transport)

    def connection_lost(self, exc: Optional[BaseException]) -> None:
        self._loop._wrap_cb(self.protocol.connection_lost, exc)
        self.wait_closed.set_result(None)

    def pause_writing(self) -> None:
        self._loop._wrap_cb(self.protocol.pause_writing)

    def resume_writing(self) -> None:
        self._loop._wrap_cb(self.protocol.resume_writing)


class _ProtocolProxy(_BaseProtocolProxy, asyncio.Protocol):
    def data_received(self, data: bytes) -> None:
        protocol = cast(asyncio.Protocol, self.protocol)
        self._loop._wrap_cb(protocol.data_received, data)

    def eof_received(self) -> None:
        protocol = cast(asyncio.Protocol, self.protocol)
        self._loop._wrap_cb(protocol.eof_received)


class _BufferedProtocolProxy(_BaseProtocolProxy, asyncio.BufferedProtocol):
    def get_buffer(self, sizehint: int) -> bytearray:
        protocol = cast(asyncio.BufferedProtocol, self.protocol)
        return self._loop._wrap_cb(protocol.get_buffer, sizehint)

    def buffer_updated(self, nbytes: int) -> None:
        protocol = cast(asyncio.BufferedProtocol, self.protocol)
        self._loop._wrap_cb(protocol.buffer_updated, nbytes)

    def eof_received(self) -> None:
        protocol = cast(asyncio.BufferedProtocol, self.protocol)
        self._loop._wrap_cb(protocol.eof_received)  # type: ignore[attr-defined]


class _UniversalProtocolProxy(_BufferedProtocolProxy, _ProtocolProxy):
    pass


class _DatagramProtocolProxy(_BaseProtocolProxy, asyncio.DatagramProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        # asyncio has wrong DatagramTransport inheritance, auto-selection by
        # original type doesn't work.
        # See https://bugs.python.org/issue46194
        self.transport = _DatagramTransportProxy(transport, self._loop)
        self._loop._wrap_cb(self.protocol.connection_made, self.transport)

    def datagram_received(self, data: bytes, addr: Any) -> None:
        protocol = cast(asyncio.DatagramProtocol, self.protocol)
        self._loop._wrap_cb(protocol.datagram_received, data, addr)

    def error_received(self, exc: BaseException) -> None:
        protocol = cast(asyncio.DatagramProtocol, self.protocol)
        self._loop._wrap_cb(protocol.error_received, exc)


class _SubprocessProtocolProxy(_BaseProtocolProxy, asyncio.SubprocessProtocol):
    def pipe_data_received(self, fd: int, data: bytes) -> None:
        protocol = cast(asyncio.SubprocessProtocol, self.protocol)
        self._loop._wrap_cb(protocol.pipe_data_received, fd, data)

    def pipe_connection_lost(self, fd: int, exc: Optional[BaseException]) -> None:
        protocol = cast(asyncio.SubprocessProtocol, self.protocol)
        self._loop._wrap_cb(protocol.pipe_connection_lost, fd, exc)

    def process_exited(self) -> None:
        protocol = cast(asyncio.SubprocessProtocol, self.protocol)
        self._loop._wrap_cb(protocol.process_exited)


_MAP = (
    (asyncio.SubprocessProtocol, _SubprocessProtocolProxy),
    (asyncio.DatagramProtocol, _DatagramProtocolProxy),
    (asyncio.BufferedProtocol, _BufferedProtocolProxy),
    (asyncio.Protocol, _ProtocolProxy),
    (asyncio.BaseProtocol, _BaseProtocolProxy),
)


def _proto_proxy(
    original: asyncio.BaseProtocol, loop: "LoopProxy"
) -> _BaseProtocolProxy:
    if isinstance(original, asyncio.BufferedProtocol) and isinstance(
        original, asyncio.Protocol
    ):
        return _UniversalProtocolProxy(original, loop)
    for orig_type, proxy_type in _MAP:
        if isinstance(original, orig_type):
            return proxy_type(original, loop)
    else:
        raise RuntimeError(f"Cannot find protocol proxy for {original!r}")


def _proto_proxy_factory(
    original_factory: Callable[[], asyncio.BaseProtocol],
    loop: "LoopProxy",
) -> Callable[[], _BaseProtocolProxy]:
    def factory() -> _BaseProtocolProxy:
        original = original_factory()
        return _proto_proxy(original, loop)

    return factory

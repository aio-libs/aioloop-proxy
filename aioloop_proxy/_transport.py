import asyncio
from typing import TYPE_CHECKING, Any, List, Optional, Tuple, cast

if TYPE_CHECKING:
    from ._loop import LoopProxy


class _BaseTransportProxy(asyncio.BaseTransport):
    def __init__(self, original: asyncio.BaseTransport, loop: "LoopProxy"):
        self._loop = loop
        self._orig = original

    def __repr__(self) -> str:
        return repr(self._orig)

    def __del__(self) -> None:
        # Cleanup original transport, raise ResourceWarning early if needed
        del self._loop
        del self._orig

    def __getattr__(self, name: str) -> Any:
        return getattr(self._orig, name)

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self._orig.get_extra_info(name, default)

    def is_closing(self) -> bool:
        return self._orig.is_closing()

    def close(self) -> None:
        return self._orig.close()

    def set_protocol(self, protocol: asyncio.BaseProtocol) -> None:
        from ._protocol import _proto_proxy

        return self._orig.set_protocol(_proto_proxy(protocol, self._loop))

    def get_protocol(self) -> asyncio.BaseProtocol:
        original = self._orig
        if original is None:
            # A possible situaton during the transport cleanup
            return None
        else:
            orig_proto = original.get_protocol()
            if orig_proto is None:
                return None
            else:
                return orig_proto.protocol  # type: ignore


class _ReadTransportProxy(_BaseTransportProxy, asyncio.ReadTransport):
    def is_reading(self) -> bool:
        orig = cast(asyncio.ReadTransport, self._orig)
        return orig.is_reading()

    def pause_reading(self) -> None:
        orig = cast(asyncio.ReadTransport, self._orig)
        return orig.pause_reading()

    def resume_reading(self) -> None:
        orig = cast(asyncio.ReadTransport, self._orig)
        return orig.resume_reading()


class _WriteTransportProxy(_BaseTransportProxy, asyncio.WriteTransport):
    def set_write_buffer_limits(
        self, high: Optional[int] = None, low: Optional[int] = None
    ) -> None:
        orig = cast(asyncio.WriteTransport, self._orig)
        return orig.set_write_buffer_limits(high, low)

    def get_write_buffer_size(self) -> int:
        orig = cast(asyncio.WriteTransport, self._orig)
        return orig.get_write_buffer_size()

    def get_write_buffer_limits(self) -> Tuple[int, int]:
        orig = cast(asyncio.WriteTransport, self._orig)
        return orig.get_write_buffer_limits()  # type: ignore

    def write(self, data: bytes) -> None:
        orig = cast(asyncio.WriteTransport, self._orig)
        return orig.write(data)

    def writelines(self, list_of_data: List[bytes]) -> None:
        orig = cast(asyncio.WriteTransport, self._orig)
        return orig.writelines(list_of_data)

    def write_eof(self) -> None:
        orig = cast(asyncio.WriteTransport, self._orig)
        return orig.write_eof()

    def can_write_eof(self) -> bool:
        orig = cast(asyncio.WriteTransport, self._orig)
        return orig.can_write_eof()

    def abort(self) -> None:
        orig = cast(asyncio.WriteTransport, self._orig)
        return orig.abort()


class _TransportProxy(_ReadTransportProxy, _WriteTransportProxy, asyncio.Transport):
    pass


class _DatagramTransportProxy(_BaseTransportProxy, asyncio.DatagramTransport):
    def sendto(self, data: bytes, addr: Any = None) -> None:
        orig = cast(asyncio.DatagramTransport, self._orig)
        return orig.sendto(data, addr)

    def abort(self) -> None:
        orig = cast(asyncio.DatagramTransport, self._orig)
        orig.abort()


class _SubprocessTransportProxy(_BaseTransportProxy, asyncio.SubprocessTransport):
    def get_pid(self) -> int:
        orig = cast(asyncio.SubprocessTransport, self._orig)
        return orig.get_pid()

    def get_returncode(self) -> Optional[int]:
        orig = cast(asyncio.SubprocessTransport, self._orig)
        return orig.get_returncode()

    def get_pipe_transport(self, fd: int) -> Optional[asyncio.BaseTransport]:
        orig = cast(asyncio.SubprocessTransport, self._orig)
        transp = orig.get_pipe_transport(fd)
        if transp is None:
            return None
        return _make_transport_proxy(transp, self._loop)

    def send_signal(self, signal: int) -> int:
        orig = cast(asyncio.SubprocessTransport, self._orig)
        return orig.send_signal(signal)

    def terminate(self) -> None:
        orig = cast(asyncio.SubprocessTransport, self._orig)
        orig.terminate()

    def kill(self) -> None:
        orig = cast(asyncio.SubprocessTransport, self._orig)
        orig.kill()


_MAP = (
    (asyncio.Transport, _TransportProxy),
    (asyncio.WriteTransport, _WriteTransportProxy),
    (asyncio.ReadTransport, _ReadTransportProxy),
    (asyncio.SubprocessTransport, _SubprocessTransportProxy),
    (asyncio.DatagramTransport, _DatagramTransportProxy),
    (asyncio.BaseTransport, _BaseTransportProxy),
)


def _make_transport_proxy(
    original: asyncio.BaseTransport, loop: "LoopProxy"
) -> _BaseTransportProxy:
    for orig_type, proxy_type in _MAP:
        if isinstance(original, orig_type):
            return proxy_type(original, loop)
    else:
        raise RuntimeError(f"Cannot find transport proxy for {original!r}")

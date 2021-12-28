import asyncio

from ._transport import _make_transport_proxy


class _BaseProtocolProxy(asyncio.BaseProtocol):
    def __init__(self, loop, protocol):
        self._loop = loop
        self.protocol = protocol
        self.transport = None

    def connection_made(self, transport):
        self.transport = _make_transport_proxy(transport, self._loop)
        self._loop._wrap_sync_proto(self.protocol.connection_made, self.transport)

    def connection_lost(self, exc):
        self._loop._wrap_sync_proto(self.protocol.connection_lost, exc)

    def pause_writing(self):
        self._loop._wrap_sync_proto(self.protocol.pause_writing)

    def resume_writing(self):
        self._loop._wrap_sync_proto(self.protocol.resume_writing)


class _ProtocolProxy(_BaseProtocolProxy, asyncio.Protocol):
    def data_received(self, data):
        self._loop._wrap_sync_proto(self.protocol.data_received, data)

    def eof_received(self):
        self._loop._wrap_sync_proto(self.protocol.eof_received)


class _BufferedProtocolProxy(_BaseProtocolProxy, asyncio.BufferedProtocol):
    def get_buffer(self, sizehint):
        return self._loop._wrap_sync_proto(self.protocol.get_buffer, sizehint)

    def buffer_updated(self, nbytes):
        self._loop._wrap_sync_proto(self.protocol.buffer_updated, nbytes)

    def eof_received(self):
        self._loop._wrap_sync_proto(self.protocol.eof_received)


class _UniversalProtocolProxy(_BufferedProtocolProxy, _ProtocolProxy):
    pass


class _DatagramProtocolProxy(_BaseProtocolProxy, asyncio.DatagramProtocol):
    def datagram_received(self, data, addr):
        self._loop._wrap_sync_proto(self.protocol.datagram_received, data, addr)

    def error_received(self, exc):
        self._loop._wrap_sync_proto(self.protocol.error_received, exc)


class _SubprocessProtocolProxy(_BaseProtocolProxy, asyncio.SubprocessProtocol):
    def pipe_data_received(self, fd, data):
        self._loop._wrap_sync_proto(self.protocol.pipe_data_received, fd, data)

    def pipe_connection_lost(self, fd, exc):
        self._loop._wrap_sync_proto(self.protocol.pipe_connection_lost, fd, exc)

    def process_exited(self):
        self._loop._wrap_sync_proto(self.protocol.process_exited)


_MAP = (
    (asyncio.BufferedProtocol, _BufferedProtocolProxy),
    (asyncio.Protocol, _ProtocolProxy),
    (asyncio.SubprocessProtocol, _SubprocessProtocolProxy),
    (asyncio.DatagramProtocol, _DatagramProtocolProxy),
    (asyncio.BaseProtocol, _BaseProtocolProxy),
)


def _proto_proxy(original, loop):
    if isinstance(original, asyncio.BufferedProtocol) and isinstance(
        original, asyncio.Protocol
    ):
        return _UniversalProtocolProxy(loop, original)
    for orig_type, proxy_type in _MAP:
        if isinstance(original, orig_type):
            return proxy_type(loop, original)
    else:
        raise RuntimeError(f"Cannot find protocol proxy for {original!r}")


def _proto_proxy_factory(original_factory, loop):
    def factory():
        original = original_factory()
        return _proto_proxy(original, loop)

    return factory

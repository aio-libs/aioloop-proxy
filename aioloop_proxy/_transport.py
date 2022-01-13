import asyncio


class _BaseTransportProxy(asyncio.BaseTransport):
    def __init__(self, original, loop):
        self._loop = loop
        self._orig = original

    def __repr__(self):
        return repr(self._orig)

    def __del__(self):
        # Cleanup original transport, raise ResourceWarning early if needed
        self._loop = None
        self._orig = None

    def get_extra_info(self, name, default=None):
        return self._orig.get_extra_info(name, default)

    def is_closing(self):
        return self._orig.is_closing()

    def close(self):
        return self._orig.close()

    def set_protocol(self, protocol):
        from ._protocol import _proto_proxy

        return self._orig.set_protocol(_proto_proxy(protocol, self._loop))

    def get_protocol(self):
        original = self._orig
        if original is None:
            # A possible situaton during the transport cleanup
            return None
        else:
            orig_proto = original.get_protocol()
            if orig_proto is None:
                return None
            else:
                return orig_proto.protocol


class _ReadTransportProxy(_BaseTransportProxy, asyncio.ReadTransport):
    def is_reading(self):
        return self._orig.is_reading()

    def pause_reading(self):
        return self._orig.pause_reading()

    def resume_reading(self):
        return self._orig.resume_reading()


class _WriteTransportProxy(_BaseTransportProxy, asyncio.WriteTransport):
    def set_write_buffer_limits(self, high=None, low=None):
        return self._orig.set_write_buffer_limits(high, low)

    def get_write_buffer_size(self):
        return self._orig.get_write_buffer_size()

    def get_write_buffer_limits(self):
        return self._orig.get_write_buffer_limits()

    def write(self, data):
        return self._orig.write(data)

    def writelines(self, list_of_data):
        return self._orig.writelines(list_of_data)

    def write_eof(self):
        return self._orig.write_eof()

    def can_write_eof(self):
        return self._orig.can_write_eof()

    def abort(self):
        return self._orig.abort()


class _TransportProxy(_ReadTransportProxy, _WriteTransportProxy, asyncio.Transport):
    pass


class _DatagramTransportProxy(_BaseTransportProxy, asyncio.DatagramTransport):
    def sendto(self, data, addr=None):
        return self._orig.sendto(data, addr)

    def abort(self):
        return self._orig.abort()


class _SubprocessTransportProxy(_BaseTransportProxy, asyncio.SubprocessTransport):
    def get_pid(self):
        return self._orig.get_pid()

    def get_returncode(self):
        return self._orig.get_returncode()

    def get_pipe_transport(self, fd):
        transp = self._orig.get_pipe_transport(fd)
        return _make_transport_proxy(transp, self._loop)

    def send_signal(self, signal):
        return self._orig.send_signal(signal)

    def terminate(self):
        return self._orig.terminate()

    def kill(self):
        return self._orig.kill()


_MAP = (
    (asyncio.Transport, _TransportProxy),
    (asyncio.WriteTransport, _WriteTransportProxy),
    (asyncio.ReadTransport, _ReadTransportProxy),
    (asyncio.SubprocessTransport, _SubprocessTransportProxy),
    (asyncio.DatagramTransport, _DatagramTransportProxy),
    (asyncio.BaseTransport, _BaseTransportProxy),
)


def _make_transport_proxy(original, loop):
    for orig_type, proxy_type in _MAP:
        if isinstance(original, orig_type):
            return proxy_type(original, loop)
    else:
        raise RuntimeError(f"Cannot find transport proxy for {original!r}")

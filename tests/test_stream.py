import asyncio
import pathlib
import secrets
import socket
import ssl
import sys
import tempfile
import unittest

import trustme

import aioloop_proxy

_loop = None


def setUpModule():
    global _loop
    _loop = asyncio.new_event_loop()


def tearDownModule():
    global _loop
    if hasattr(_loop, "shutdown_default_executor"):
        _loop.run_until_complete(_loop.shutdown_default_executor())
    _loop.run_until_complete(_loop.shutdown_asyncgens())
    _loop.close()
    _loop = None


class SrvProto(asyncio.Protocol):
    def __init__(self, case, ssl_srv=None):
        self.case = case
        self.loop = case.loop
        self.transp = None
        self.events = set()
        self.eof = False
        self.buf = bytearray(0x10000)
        self.ssl_srv = ssl_srv
        self.closed = self.loop.create_future()

    def connection_made(self, transport):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.transp = transport
        self.case.assertIsInstance(transport, asyncio.Transport)
        transport.write(b"CONNECTED\n")
        self.events.add("MADE")

    def get_buffer(self, sizehint):
        return memoryview(self.buf)

    def buffer_updated(self, nbytes):
        self.data_received(self.buf[:nbytes])

    def data_received(self, data):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.transp.write(b"ACK:" + data)
        if data == b"STOP\n":
            self.eof = True
            self.transp.write_eof()
        elif data == b"UPGRADE\n":
            self.events.add("UPGRADE")
            self.task = self.loop.create_task(
                self.loop.start_tls(self.transp, self, self.ssl_srv, server_side=True)
            )
            self.task.add_done_callback(self._ssl_upgraded)
        self.events.add("DATA")

    def _ssl_upgraded(self, fut):
        self.transp = fut.result()
        self.transp.write(b"UPGRADED\n")
        self.events.add("UPGRADED")

    def eof_received(self):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("EOF")
        if not self.eof:
            self.transp.write(b"EOF\n")

    def connection_lost(self, exc):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("LOST")
        self.transp.close()
        if not self.closed.done():
            self.closed.set_result(None)


class SrvBufferedProto(asyncio.BufferedProtocol):
    def __init__(self, case):
        self.case = case
        self.loop = case.loop
        self.transp = None
        self.events = set()
        self.eof = False
        self.buf = bytearray(0x10000)

    def connection_made(self, transport):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.transp = transport
        self.case.assertIsInstance(transport, asyncio.Transport)
        transport.write(b"CONNECTED\n")
        self.events.add("MADE")

    def get_buffer(self, sizehint):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("GET_BUFFER")
        return memoryview(self.buf)

    def buffer_updated(self, nbytes):
        data = self.buf[:nbytes]
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.transp.write(b"ACK:" + data)
        if data == b"STOP\n":
            self.eof = True
            self.transp.write_eof()
        self.events.add("BUFFER_UPDATED")

    def eof_received(self):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("EOF")
        if not self.eof:
            self.transp.write(b"EOF\n")

    def connection_lost(self, exc):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("LOST")
        self.transp.close()


class CliProto(asyncio.Protocol):
    def __init__(self, case):
        self.case = case
        self.loop = case.loop
        self.transp = None
        self.events = set()
        self.closed = self.loop.create_future()
        self._recv = self.loop.create_future()
        self._data = []

    def connection_made(self, transport):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.transp = transport
        self.case.assertIsInstance(transport, asyncio.Transport)
        self.events.add("MADE")

    def data_received(self, data):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("DATA")
        self._data.append(data)
        if not self._recv.done():
            self._recv.set_result(None)

    def eof_received(self):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("EOF")

    def connection_lost(self, exc):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("LOST")
        self.transp.close()
        self.closed.set_result(None)

    async def recv(self):
        try:
            await self._recv
            ret = b"".join(self._data)
            self._data.clear()
            return ret
        finally:
            self._recv = self.loop.create_future()


class CliBufferedProto(asyncio.BufferedProtocol):
    def __init__(self, case):
        self.case = case
        self.loop = case.loop
        self.transp = None
        self.events = set()
        self.closed = self.loop.create_future()
        self._recv = self.loop.create_future()
        self._buffer = bytearray(0x10000)
        self._offset = 0

    def connection_made(self, transport):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.transp = transport
        self.case.assertIsInstance(transport, asyncio.Transport)
        self.events.add("MADE")

    def get_buffer(self, sizehint):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("GET_BUFFER")
        if sizehint != -1:
            sizehint = 0x8000
        if sizehint + self._offset > len(self._buffer):
            self._buffer.extend(bytearray(sizehint * 2 + self._offset))
        return memoryview(self._buffer)[self._offset :]

    def buffer_updated(self, nbytes):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("BUFFER_UPDATED")
        self._offset += nbytes
        self._recv.set_result(None)

    def eof_received(self):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("EOF")

    def connection_lost(self, exc):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("LOST")
        self.transp.close()
        self.closed.set_result(None)

    async def recv(self):
        try:
            await self._recv
            ret = self._buffer[: self._offset]
            del self._buffer[: self._offset]
            self._offset = 0
            return ret
        finally:
            self._recv = self.loop.create_future()


class TestTCP(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def check_server_by_client(self, server):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        addr = server.sockets[0].getsockname()
        sock.connect(addr)
        data = sock.recv(1024)
        self.assertEqual(b"CONNECTED\n", data)
        sock.sendall(b"1\n")
        data = sock.recv(1024)
        self.assertEqual(b"ACK:1\n", data)
        sock.sendall(b"2\n")
        data = sock.recv(1024)
        self.assertEqual(b"ACK:2\n", data)
        sock.shutdown(socket.SHUT_WR)
        data = sock.recv(1024)
        self.assertEqual(b"EOF\n", data)
        sock.close()

    async def run_server(self, server):
        self.assertEqual(repr(server), repr(server._orig))
        self.assertIsInstance(server, asyncio.AbstractServer)
        self.assertIs(server.get_loop(), self.loop)
        self.assertIsInstance(server._orig, asyncio.AbstractServer)
        self.assertFalse(server.is_serving())
        await server.start_serving()
        self.assertTrue(server.is_serving())

        await self.loop.run_in_executor(None, self.check_server_by_client, server)
        server.close()
        await server.wait_closed()

    def test_create_server(self):
        async def f():
            proto = SrvProto(self)
            server = await self.loop.create_server(
                lambda: proto, host="127.0.0.1", port=0, start_serving=False
            )
            await self.run_server(server)
            self.assertSetEqual(proto.events, {"MADE", "DATA", "EOF", "LOST"})

        self.loop.run_until_complete(f())

    def test_create_buffered_server(self):
        async def f():
            proto = SrvBufferedProto(self)
            server = await self.loop.create_server(
                lambda: proto, host="127.0.0.1", port=0, start_serving=False
            )
            await self.run_server(server)
            self.assertSetEqual(
                proto.events, {"MADE", "GET_BUFFER", "BUFFER_UPDATED", "EOF", "LOST"}
            )

        self.loop.run_until_complete(f())

    def test_serve_forever(self):
        def g(server):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            addr = server.sockets[0].getsockname()
            sock.connect(addr)
            data = sock.recv(1024)
            self.assertEqual(b"CONNECTED\n", data)
            sock.close()

        async def f():
            proto = SrvProto(self)
            server = await self.loop.create_server(
                lambda: proto, host="127.0.0.1", port=0
            )

            server_task = self.loop.create_task(server.serve_forever())
            await self.loop.run_in_executor(None, g, server)
            server_task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await server_task

            self.assertFalse(server.is_serving())

        self.loop.run_until_complete(f())

    async def connect_and_test(self, cli_proto_factory):
        proto = SrvProto(self)
        server = await self.loop.create_server(
            lambda: proto, host="127.0.0.1", port=0, start_serving=False
        )
        await server.start_serving()
        addr = server.sockets[0].getsockname()
        host, port = addr[:2]

        tr, pr = await self.loop.create_connection(cli_proto_factory, host, port)
        self.assertEqual(repr(tr), repr(tr._orig))
        self.assertEqual(tr.get_extra_info("peername"), addr)
        self.assertFalse(tr.is_closing())
        self.assertTrue(tr.can_write_eof())

        data = await pr.recv()
        self.assertEqual(b"CONNECTED\n", data)
        tr.write(b"1\n")
        data = await pr.recv()
        self.assertEqual(b"ACK:1\n", data)
        tr.write(b"2\n")
        data = await pr.recv()
        self.assertEqual(b"ACK:2\n", data)
        tr.writelines([b"da", b"ta\n"])
        data = await pr.recv()
        self.assertEqual(b"ACK:data\n", data)

        tr.write(b"STOP\n")
        await asyncio.sleep(0)  # wait for eof_received() processing

        tr.close()
        self.assertTrue(tr.is_closing())
        await pr.closed

        server.close()
        await server.wait_closed()
        return pr

    def test_connect(self):
        async def f():
            pr = await self.connect_and_test(lambda: CliProto(self))
            self.assertSetEqual(pr.events, {"MADE", "DATA", "LOST"})

        self.loop.run_until_complete(f())

    def test_connect_buffered(self):
        async def f():
            pr = await self.connect_and_test(lambda: CliBufferedProto(self))
            self.assertSetEqual(
                pr.events, {"MADE", "GET_BUFFER", "BUFFER_UPDATED", "LOST"}
            )

        self.loop.run_until_complete(f())

    def test_abort(self):
        async def f():
            proto = SrvProto(self)
            server = await self.loop.create_server(
                lambda: proto, host="127.0.0.1", port=0, start_serving=False
            )
            await server.start_serving()
            addr = server.sockets[0].getsockname()
            host, port = addr[:2]

            tr, pr = await self.loop.create_connection(
                lambda: CliProto(self), host, port
            )
            tr.abort()
            self.assertTrue(tr.is_closing())
            await pr.closed

            server.close()
            await server.wait_closed()

            self.assertSetEqual(pr.events, {"MADE", "DATA", "LOST"})

        self.loop.run_until_complete(f())

    def test_pause_resume_reader(self):
        async def f():
            proto = SrvProto(self)
            server = await self.loop.create_server(
                lambda: proto, host="127.0.0.1", port=0, start_serving=False
            )
            await server.start_serving()
            addr = server.sockets[0].getsockname()
            host, port = addr[:2]

            tr, pr = await self.loop.create_connection(
                lambda: CliProto(self), host, port
            )
            self.assertTrue(tr.is_reading())
            tr.pause_reading()
            self.assertFalse(tr.is_reading())
            tr.resume_reading()
            self.assertTrue(tr.is_reading())
            tr.close()
            self.assertTrue(tr.is_closing())
            await pr.closed

            server.close()
            await server.wait_closed()

        self.loop.run_until_complete(f())

    def test_write_buffer_limits(self):
        async def f():
            proto = SrvProto(self)
            server = await self.loop.create_server(
                lambda: proto, host="127.0.0.1", port=0, start_serving=False
            )
            await server.start_serving()
            addr = server.sockets[0].getsockname()
            host, port = addr[:2]

            tr, pr = await self.loop.create_connection(
                lambda: CliProto(self), host, port
            )
            low0, high0 = tr.get_write_buffer_limits()
            size = tr.get_write_buffer_size()
            self.assertEqual(size, 0)  # write queue is empty
            low1 = low0 // 2
            high1 = high0 // 2
            tr.set_write_buffer_limits(high1, low1)
            low2, high2 = tr.get_write_buffer_limits()
            self.assertEqual(low1, low2)
            self.assertEqual(high1, high2)
            tr.set_write_buffer_limits(high0, low0)
            tr.close()
            self.assertTrue(tr.is_closing())
            await pr.closed

            server.close()
            await server.wait_closed()

            self.assertSetEqual(pr.events, {"MADE", "DATA", "LOST"})

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows behavior is really strange here")
    def test_connect_accepted_socket(self):
        async def client(host, port):
            tr, pr = await self.loop.create_connection(
                lambda: CliProto(self), host, port
            )
            data = await pr.recv()
            self.assertEqual(b"CONNECTED\n", data)
            tr.write(b"1\n")
            data = await pr.recv()
            self.assertEqual(b"ACK:1\n", data)
            tr.write(b"2\n")
            data = await pr.recv()
            self.assertEqual(b"ACK:2\n", data)
            tr.writelines([b"da", b"ta\n"])
            data = await pr.recv()
            self.assertEqual(b"ACK:data\n", data)

            tr.write(b"STOP\n")
            await asyncio.sleep(0)  # wait for eof_received() processing

            tr.close()
            self.assertTrue(tr.is_closing())
            await pr.closed

        async def f():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sock.listen(10)
            addr = sock.getsockname()
            proto = SrvProto(self)
            server = await self.loop.create_server(
                lambda: proto,
                host="127.0.0.1",
                port=0,
            )
            addr = server.sockets[0].getsockname()
            host, port = addr

            task = self.loop.create_task(client(host, port))

            tr, pr = await self.loop.connect_accepted_socket(lambda: proto, sock)
            self.assertIs(pr, proto)
            self.assertIs(tr, proto.transp)

            await task

            server.close()
            await server.wait_closed()
            self.assertSetEqual(proto.events, {"MADE", "DATA", "LOST"})

        self.loop.run_until_complete(f())

    @unittest.skipIf(
        sys.platform == "win32" and sys.version_info < (3, 8),
        "sendfile is buggy for Python 3.7 on Windows",
    )
    def test_sendfile(self):
        async def f():
            proto = SrvProto(self)
            server = await self.loop.create_server(
                lambda: proto, host="127.0.0.1", port=0
            )
            addr = server.sockets[0].getsockname()
            host, port = addr[:2]

            tr, pr = await self.loop.create_connection(
                lambda: CliProto(self), host, port
            )
            data = await pr.recv()
            self.assertEqual(b"CONNECTED\n", data)
            fname = pathlib.Path(__file__)
            body = fname.read_bytes()
            with fname.open("rb") as fp:
                sent = await self.loop.sendfile(tr, fp)
            self.assertEqual(sent, len(body))
            ret = await pr.recv()
            expected = b"ACK:" + body
            self.assertEqual(len(ret), len(expected))
            self.assertEqual(ret, expected)
            tr.close()
            self.assertTrue(tr.is_closing())
            await pr.closed

            server.close()
            await server.wait_closed()

            self.assertSetEqual(pr.events, {"MADE", "DATA", "LOST"})

        self.loop.run_until_complete(f())

    def make_certs(self):
        CA = trustme.CA()
        cert = CA.issue_cert(
            "127.0.0.1",
            "::1",
        )
        ssl_srv = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert.configure_cert(ssl_srv)
        ssl_cli = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        CA.configure_trust(ssl_cli)
        return ssl_srv, ssl_cli

    def test_start_tls(self):
        ssl_srv, ssl_cli = self.make_certs()

        async def f():
            proto = SrvProto(self, ssl_srv)
            server = await self.loop.create_server(
                lambda: proto, host="127.0.0.1", port=0
            )
            addr = server.sockets[0].getsockname()
            host, port = addr[:2]

            tr, pr = await self.loop.create_connection(
                lambda: CliProto(self), host, port
            )
            data = await pr.recv()
            self.assertEqual(data, b"CONNECTED\n")
            tr.write(b"1\n")
            data = await pr.recv()
            self.assertEqual(data, b"ACK:1\n")

            tr.write(b"UPGRADE\n")
            data = await pr.recv()
            self.assertEqual(data, b"ACK:UPGRADE\n")
            tr2 = await self.loop.start_tls(tr, pr, ssl_cli)
            data = await pr.recv()
            self.assertEqual(data, b"UPGRADED\n")

            tr2.write(b"2\n")
            data = await pr.recv()
            self.assertEqual(data, b"ACK:2\n")
            tr2.close()
            self.assertTrue(tr2.is_closing())
            await pr.closed

            await proto.closed

            server.close()
            await server.wait_closed()

            self.assertSetEqual(pr.events, {"MADE", "DATA", "EOF", "LOST"})
            self.assertSetEqual(
                proto.events, {"MADE", "DATA", "UPGRADE", "UPGRADED", "LOST"}
            )

        self.loop.run_until_complete(f())


@unittest.skipIf(sys.platform == "win32", "Windows has no UNIX sockets")
class TestUNIX(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    async def connect_and_test(self, cli_proto_factory):
        proto = SrvProto(self)
        path = (
            pathlib.Path(tempfile.gettempdir())
            / f"aioloop-proxy-{secrets.token_hex(4)}.sock"
        )
        server = await self.loop.create_unix_server(lambda: proto, path=str(path))
        addr = server.sockets[0].getsockname()
        host, port = addr[:2]

        tr, pr = await self.loop.create_unix_connection(cli_proto_factory, path=path)
        self.assertEqual(repr(tr), repr(tr._orig))
        self.assertEqual(tr.get_extra_info("peername"), addr)
        self.assertFalse(tr.is_closing())
        self.assertTrue(tr.can_write_eof())

        data = await pr.recv()
        self.assertEqual(b"CONNECTED\n", data)
        tr.write(b"1\n")
        data = await pr.recv()
        self.assertEqual(b"ACK:1\n", data)
        tr.write(b"2\n")
        data = await pr.recv()
        self.assertEqual(b"ACK:2\n", data)
        tr.writelines([b"da", b"ta\n"])
        data = await pr.recv()
        self.assertEqual(b"ACK:data\n", data)

        tr.write(b"STOP\n")
        await asyncio.sleep(0)  # wait for eof_received() processing

        tr.close()
        self.assertTrue(tr.is_closing())
        await pr.closed

        server.close()
        await server.wait_closed()
        return pr

    def test_unix_connect(self):
        async def f():
            pr = await self.connect_and_test(lambda: CliProto(self))
            self.assertSetEqual(pr.events, {"MADE", "DATA", "LOST"})

        self.loop.run_until_complete(f())

    def test_unix_connect_buffered(self):
        async def f():
            pr = await self.connect_and_test(lambda: CliBufferedProto(self))
            self.assertSetEqual(
                pr.events, {"MADE", "GET_BUFFER", "BUFFER_UPDATED", "LOST"}
            )

        self.loop.run_until_complete(f())


if __name__ == "__main__":
    unittest.main()

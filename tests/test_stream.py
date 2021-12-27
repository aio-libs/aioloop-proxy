import asyncio
import socket
import unittest

import aioloop_proxy

_loop = None


def setUpModule():
    global _loop
    _loop = asyncio.new_event_loop()


def tearDownModule():
    global _loop
    _loop.run_until_complete(_loop.shutdown_default_executor())
    _loop.run_until_complete(_loop.shutdown_asyncgens())
    _loop.close()
    _loop = None


class SrvProto(asyncio.Protocol):
    def __init__(self, case):
        self.case = case
        self.loop = case.loop
        self.transp = None
        self.events = set()

    def connection_made(self, transport):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.transp = transport
        self.case.assertIsInstance(transport, asyncio.Transport)
        transport.write(b"CONNECTED\n")
        self.events.add("MADE")

    def data_received(self, data):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.transp.write(b"ACK:" + data)
        self.events.add("DATA")

    def eof_received(self):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.transp.write(b"EOF\n")
        self.events.add("EOF")

    def connection_lost(self, exc):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("LOST")


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
        self._recv.set_result(None)

    def eof_received(self):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("EOF")

    def connection_lost(self, exc):
        loop = asyncio.get_running_loop()
        self.case.assertIs(loop, self.loop)
        self.events.add("LOST")
        self.closed.set_result(None)

    async def recv(self):
        try:
            await self._recv
            ret = b"".join(self._data)
            self._data.clear()
            return ret
        finally:
            self._recv = self.loop.create_future()


class TestTCP(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.check_resouces(strict=True)
        self.loop.close()

    def test_create_server(self):
        def g(server):
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

            server.close()

        async def f():
            proto = SrvProto(self)
            server = await self.loop.create_server(
                lambda: proto, host="localhost", port=0, start_serving=False
            )
            self.assertEqual(repr(server), repr(server._orig))
            self.assertIsInstance(server, asyncio.AbstractServer)
            self.assertIs(server.get_loop(), self.loop)
            self.assertIsInstance(server._orig, asyncio.AbstractServer)
            self.assertFalse(server.is_serving())
            await server.start_serving()
            self.assertTrue(server.is_serving())

            await self.loop.run_in_executor(None, g, server)
            await server.wait_closed()

            self.assertSetEqual(proto.events, {"MADE", "DATA", "EOF", "LOST"})

        self.loop.run_until_complete(f())

    def test_connect(self):
        async def f():
            proto = SrvProto(self)
            server = await self.loop.create_server(
                lambda: proto, host="localhost", port=0, start_serving=False
            )
            await server.start_serving()
            addr = server.sockets[0].getsockname()
            host, port = addr[:2]

            tr, pr = await self.loop.create_connection(
                lambda: CliProto(self), host, port
            )
            self.assertEqual(repr(tr), repr(tr._orig))
            self.assertEqual(tr.get_extra_info("peername"), addr)
            self.assertFalse(tr.is_closing())
            data = await pr.recv()
            self.assertEqual(b"CONNECTED\n", data)
            tr.write(b"1\n")
            data = await pr.recv()
            self.assertEqual(b"ACK:1\n", data)
            tr.write(b"2\n")
            data = await pr.recv()
            self.assertEqual(b"ACK:2\n", data)
            tr.write_eof()
            data = await pr.recv()
            self.assertEqual(b"EOF\n", data)
            tr.close()
            self.assertTrue(tr.is_closing())
            await pr.closed

            server.close()
            await server.wait_closed()

            self.assertSetEqual(pr.events, {"MADE", "DATA", "LOST"})

        self.loop.run_until_complete(f())

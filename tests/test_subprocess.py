import asyncio
import pathlib
import shlex
import sys
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


class Proto(asyncio.SubprocessProtocol):
    def __init__(self, case):
        self.case = case
        self.loop = case.loop
        self.transp = None
        self._recv = self.loop.create_future()
        self.events = set()
        self.closed = self.loop.create_future()
        self.exited = self.loop.create_future()

    def connection_made(self, transp):
        self.transp = transp
        self.events.add("MADE")

    def pipe_data_received(self, fd, data):
        self._recv.set_result((fd, data))
        self.events.add("PIPE-DATA")

    def pipe_connection_lost(self, fd, exc):
        if exc is None:
            self._recv.set_result((fd, b""))
        else:
            self._recv.set_exception(exc)
        self.events.add("PIPE-LOST")

    def process_exited(self):
        self.events.add("EXIT")
        self.exited.set_result(None)

    def connection_lost(self, exc):
        self.transp = None
        self.events.add("LOST")
        if exc is None:
            self.closed.set_result(None)
        else:
            self.closed.set_exception(exc)

    async def recv(self):
        try:
            return await self._recv
        finally:
            self._recv = self.loop.create_future()


class TestSubprocess(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.check_resouces(strict=True)
        self.loop.close()

    def exec_cmd(self, *args):
        script = pathlib.Path(__file__).parent / "subproc.py"
        return [sys.executable, script] + list(args)

    def shell_cmd(self, *args):
        return " ".join(shlex.quote(part) for part in self.exec_cmd(*args))

    def test_exec(self):
        async def f():
            tr, pr = await self.loop.subprocess_exec(
                lambda: Proto(self), *self.exec_cmd()
            )
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(fd, b"READY\n")

            tr.get_pipe_transport(1).write(b"DATA\n")
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data, b"ACK:DATA\n")

            tr.get_pipe_transport(1).write(b"EXIT:0\n")

            await tr.exited
            self.assertEqual(tr.get_returncode(), 0)
            tr.close()
            await tr.closed

        self.loop.run_until_complete(f())


if __name__ == "__main__":
    unittest.main()

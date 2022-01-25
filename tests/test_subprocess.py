import asyncio
import pathlib
import shlex
import signal
import sys
import unittest

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
        if not self._recv.done():
            self._recv.set_result((fd, data))
        self.events.add("PIPE-DATA")

    def pipe_connection_lost(self, fd, exc):
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


@unittest.skipIf(
    sys.version_info < (3, 8), "Subprocess support is tricky in Python 3.7"
)
class TestSubprocess(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def exec_cmd(self, *args):
        script = pathlib.Path(__file__).parent / "subproc.py"
        return [sys.executable, str(script)] + list(args)

    def shell_cmd(self, *args):
        return " ".join(shlex.quote(part) for part in self.exec_cmd(*args))

    def test_exec(self):
        async def f():
            tr, pr = await self.loop.subprocess_exec(
                lambda: Proto(self), *self.exec_cmd()
            )
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data.strip(), b"READY")

            tr.get_pipe_transport(0).write(b"DATA\n")
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data.strip(), b"ACK:DATA")

            tr.get_pipe_transport(0).write(b"EXIT:0\n")

            await pr.exited
            self.assertEqual(tr.get_returncode(), 0)
            tr.close()
            await pr.closed

            self.assertSetEqual(
                pr.events, {"MADE", "PIPE-DATA", "EXIT", "PIPE-LOST", "LOST"}
            )

        self.loop.run_until_complete(f())

    @unittest.skipIf(
        sys.platform == "win32", "Windows shell is not compliant with GitHub CI"
    )
    def test_shell(self):
        async def f():
            tr, pr = await self.loop.subprocess_shell(
                lambda: Proto(self), self.shell_cmd()
            )
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data.strip(), b"READY")

            tr.get_pipe_transport(0).write(b"DATA\n")
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data.strip(), b"ACK:DATA")

            tr.get_pipe_transport(0).write(b"EXIT:0\n")

            await pr.exited
            self.assertEqual(tr.get_returncode(), 0)
            tr.close()
            await pr.closed

            self.assertSetEqual(
                pr.events, {"MADE", "PIPE-DATA", "EXIT", "PIPE-LOST", "LOST"}
            )

        self.loop.run_until_complete(f())

    def test_stderr(self):
        async def f():
            tr, pr = await self.loop.subprocess_exec(
                lambda: Proto(self), *self.exec_cmd("--stderr")
            )
            fd, data = await pr.recv()
            self.assertEqual(fd, 2)
            self.assertEqual(data.strip(), b"READY")

            tr.get_pipe_transport(0).write(b"DATA\n")
            fd, data = await pr.recv()
            self.assertEqual(fd, 2)
            self.assertEqual(data.strip(), b"ACK:DATA")

            tr.get_pipe_transport(0).write(b"EXIT:0\n")

            await pr.exited
            self.assertEqual(tr.get_returncode(), 0)
            tr.close()
            await pr.closed

            self.assertSetEqual(
                pr.events, {"MADE", "PIPE-DATA", "EXIT", "PIPE-LOST", "LOST"}
            )

        self.loop.run_until_complete(f())

    def test_get_pid(self):
        async def f():
            tr, pr = await self.loop.subprocess_exec(
                lambda: Proto(self), *self.exec_cmd()
            )
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data.strip(), b"READY")

            tr.get_pipe_transport(0).write(b"PID\n")
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            child_pid = tr.get_pid()
            self.assertEqual(data.strip(), f"PID:{child_pid}".encode("ascii"))

            tr.get_pipe_transport(0).write(b"EXIT:0\n")
            await pr.exited
            self.assertEqual(tr.get_returncode(), 0)
            tr.close()
            await pr.closed

            self.assertSetEqual(
                pr.events, {"MADE", "PIPE-DATA", "EXIT", "PIPE-LOST", "LOST"}
            )

        self.loop.run_until_complete(f())

    def test_get_returncode(self):
        async def f():
            tr, pr = await self.loop.subprocess_exec(
                lambda: Proto(self), *self.exec_cmd()
            )
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data.strip(), b"READY")

            tr.get_pipe_transport(0).write(b"EXIT:1\n")
            await pr.exited
            self.assertEqual(tr.get_returncode(), 1)
            tr.close()
            await pr.closed

            self.assertSetEqual(
                pr.events, {"MADE", "PIPE-DATA", "EXIT", "PIPE-LOST", "LOST"}
            )

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows has no posix signals")
    def test_send_signal(self):
        async def f():
            tr, pr = await self.loop.subprocess_exec(
                lambda: Proto(self), *self.exec_cmd()
            )
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data.strip(), b"READY")

            tr.send_signal(signal.SIGINT)
            await pr.exited
            self.assertEqual(tr.get_returncode(), -signal.SIGINT)
            tr.close()
            await pr.closed

            self.assertSetEqual(
                pr.events, {"MADE", "PIPE-DATA", "EXIT", "PIPE-LOST", "LOST"}
            )

        self.loop.run_until_complete(f())

    def test_terminate(self):
        async def f():
            tr, pr = await self.loop.subprocess_exec(
                lambda: Proto(self), *self.exec_cmd()
            )
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data.strip(), b"READY")

            tr.terminate()
            await pr.exited
            if sys.platform == "win32":
                self.assertEqual(tr.get_returncode(), 1)
            else:
                self.assertEqual(tr.get_returncode(), -signal.SIGTERM)
            tr.close()
            await pr.closed

            self.assertSetEqual(
                pr.events, {"MADE", "PIPE-DATA", "EXIT", "PIPE-LOST", "LOST"}
            )

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows has no SIGKILL")
    def test_kill(self):
        async def f():
            tr, pr = await self.loop.subprocess_exec(
                lambda: Proto(self), *self.exec_cmd()
            )
            fd, data = await pr.recv()
            self.assertEqual(fd, 1)
            self.assertEqual(data.strip(), b"READY")

            tr.kill()
            await pr.exited
            self.assertEqual(tr.get_returncode(), -signal.SIGKILL)
            tr.close()
            await pr.closed

            self.assertSetEqual(
                pr.events, {"MADE", "PIPE-DATA", "EXIT", "PIPE-LOST", "LOST"}
            )

        self.loop.run_until_complete(f())

    def test_highlevel_api(self):
        async def f():
            """Starting a subprocess should be possible."""
            proc = await asyncio.subprocess.create_subprocess_exec(
                sys.executable, "--version", stdout=asyncio.subprocess.PIPE
            )
            await proc.communicate()

        self.loop.run_until_complete(f())


if __name__ == "__main__":
    unittest.main()

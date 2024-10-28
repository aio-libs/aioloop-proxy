import asyncio
import pathlib
import re
import signal
import socket
import subprocess
import sys
import unittest
from typing import Optional

import aioloop_proxy

_loop: Optional[asyncio.AbstractEventLoop] = None


def setUpModule() -> None:
    global _loop
    _loop = asyncio.new_event_loop()


def tearDownModule() -> None:
    global _loop
    assert _loop is not None
    if hasattr(_loop, "shutdown_default_executor"):
        _loop.run_until_complete(_loop.shutdown_default_executor())
    _loop.run_until_complete(_loop.shutdown_asyncgens())
    _loop.close()
    _loop = None


class TestCheckAndShutdown(unittest.TestCase):
    def setUp(self) -> None:
        assert _loop is not None
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self) -> None:
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_tasks(self) -> None:
        async def g() -> None:
            await asyncio.sleep(100)

        async def f() -> None:
            task = asyncio.create_task(g())
            with self.assertWarnsRegex(
                ResourceWarning, re.escape(f"Unfinished task {task!r}")
            ):
                await self.loop.check_and_shutdown()

            self.assertTrue(task.cancelled())

        self.loop.run_until_complete(f())

    def test_tasks_ignored(self) -> None:
        async def g() -> None:
            await asyncio.sleep(100)

        async def f() -> None:
            task = asyncio.create_task(g())
            await self.loop.check_and_shutdown(
                kind=aioloop_proxy.CheckKind.TRANSPORTS  # not TASKS
            )

            self.assertTrue(task.cancelled())

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows has no UNIX signals")
    def test_signals(self) -> None:
        async def f() -> None:
            self.loop.add_signal_handler(signal.SIGINT, lambda: None)
            with self.assertWarnsRegex(ResourceWarning, "Unregistered signal"):
                await self.loop.check_and_shutdown()

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows has no UNIX signals")
    def test_signals_ignored(self) -> None:
        async def f() -> None:
            self.loop.add_signal_handler(signal.SIGINT, lambda: None)
            await self.loop.check_and_shutdown(
                kind=aioloop_proxy.CheckKind.TRANSPORTS  # not SIGNALS
            )

        self.loop.run_until_complete(f())

    def test_server(self) -> None:
        async def f() -> None:
            async def serve(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                pass

            server = await asyncio.start_server(serve, host="127.0.0.1", port=0)
            with self.assertWarnsRegex(
                ResourceWarning, re.escape(f"Unclosed server {server!r}")
            ):
                await self.loop.check_and_shutdown()

            self.assertFalse(server.is_serving())

        self.loop.run_until_complete(f())

    def test_server_not_serving(self) -> None:
        async def f() -> None:
            async def serve(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                pass

            server = await asyncio.start_server(serve, host="127.0.0.1", port=0)
            server.close()
            await self.loop.check_and_shutdown()

            self.assertFalse(server.is_serving())

        self.loop.run_until_complete(f())

    def test_server_ignore(self) -> None:
        async def f() -> None:
            async def serve(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                pass

            server = await asyncio.start_server(serve, host="127.0.0.1", port=0)
            await self.loop.check_and_shutdown(
                kind=aioloop_proxy.CheckKind.TRANSPORTS  # not SERVERS
            )

            self.assertFalse(server.is_serving())

        self.loop.run_until_complete(f())

    def test_write_transports(self) -> None:
        async def f() -> None:
            async def serve(sock: socket.socket) -> socket.socket:
                conn, addr = await self.loop.sock_accept(sock)
                return conn

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            addr = sock.getsockname()
            sock.listen(1)
            task = self.loop.create_task(serve(sock))

            tr, pr = await self.loop.create_connection(asyncio.Protocol, *addr)
            conn = await task

            with self.assertWarnsRegex(
                ResourceWarning, re.escape(f"Unclosed transport {tr!r}")
            ):
                await self.loop.check_and_shutdown()

            self.assertTrue(tr.is_closing())
            conn.close()
            sock.close()

        self.loop.run_until_complete(f())

    def exec_cmd(self, *args: str) -> list[str]:
        script = pathlib.Path(__file__).parent / "subproc.py"
        return [sys.executable, str(script)] + list(args)

    def test_subproc_transports(self) -> None:
        async def f() -> None:
            proc = await asyncio.create_subprocess_exec(
                *self.exec_cmd(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            with self.assertWarnsRegex(
                ResourceWarning, re.escape(f"Unclosed transport {proc._transport!r}")  # type: ignore[attr-defined] # noqa
            ):
                await self.loop.check_and_shutdown()

            self.assertTrue(proc._transport.is_closing())  # type: ignore[attr-defined] # noqa

        self.loop.run_until_complete(f())

    def test_closing_transports(self) -> None:
        async def f() -> None:
            async def serve(sock: socket.socket) -> socket.socket:
                conn, addr = await self.loop.sock_accept(sock)
                return conn

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            addr = sock.getsockname()
            sock.listen(1)
            task = self.loop.create_task(serve(sock))

            tr, pr = await self.loop.create_connection(asyncio.Protocol, *addr)
            conn = await task
            tr.close()
            self.assertTrue(tr.is_closing())

            await self.loop.check_and_shutdown()

            self.assertTrue(tr.is_closing())
            conn.close()
            sock.close()

        self.loop.run_until_complete(f())

    def test_subproc_transports_ignore(self) -> None:
        async def f() -> None:
            proc = await asyncio.create_subprocess_exec(
                *self.exec_cmd(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            await self.loop.check_and_shutdown(
                kind=aioloop_proxy.CheckKind.TASKS  # not TRANSPORTS
            )

            self.assertTrue(proc._transport.is_closing())  # type: ignore[attr-defined] # noqa

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows has no readers support")
    def test_readers(self) -> None:
        async def f() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sock.setblocking(False)

            def on_read() -> None:
                pass

            self.loop.add_reader(sock, on_read)

            with self.assertWarnsRegex(
                ResourceWarning, re.escape(f"Unfinished reader {sock.fileno()}")
            ):
                await self.loop.check_and_shutdown()

            self.assertFalse(self.loop.remove_reader(sock))

            sock.close()

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows has no readers support")
    def test_readers_ignore(self) -> None:
        async def f() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sock.setblocking(False)

            def on_read() -> None:
                pass

            self.loop.add_reader(sock, on_read)

            await self.loop.check_and_shutdown(
                kind=aioloop_proxy.CheckKind.TASKS  # not READERS
            )

            self.assertFalse(self.loop.remove_reader(sock))

            sock.close()

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows has no writers support")
    def test_writers(self) -> None:
        async def f() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sock.setblocking(False)

            def on_write() -> None:
                pass

            self.loop.add_writer(sock, on_write)

            with self.assertWarnsRegex(
                ResourceWarning, re.escape(f"Unfinished writer {sock.fileno()}")
            ):
                await self.loop.check_and_shutdown()

            self.assertFalse(self.loop.remove_writer(sock))

            sock.close()

        self.loop.run_until_complete(f())

    @unittest.skipIf(sys.platform == "win32", "Windows has no writers support")
    def test_writers_ignore(self) -> None:
        async def f() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sock.setblocking(False)

            def on_write() -> None:
                pass

            self.loop.add_writer(sock, on_write)

            await self.loop.check_and_shutdown(
                kind=aioloop_proxy.CheckKind.TASKS  # not WRITERS
            )

            self.assertFalse(self.loop.remove_writer(sock))

            sock.close()

        self.loop.run_until_complete(f())

    def test_close_handle(self) -> None:
        async def f() -> None:
            handle = self.loop.call_soon(lambda: None)
            await self.loop.check_and_shutdown()

            self.assertTrue(handle.cancelled())

        self.loop.run_until_complete(f())

    def test_close_timer(self) -> None:
        async def f() -> None:
            handle = self.loop.call_later(10, lambda: None)
            await self.loop.check_and_shutdown()

            self.assertTrue(handle.cancelled())

        self.loop.run_until_complete(f())


if __name__ == "__main__":
    unittest.main()

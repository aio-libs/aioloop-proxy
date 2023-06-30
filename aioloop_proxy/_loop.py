from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
import enum
import socket
import ssl
import sys
import threading
import warnings
import weakref
from contextvars import Context
from typing import (
    IO,
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Dict,
    Generator,
    Protocol,
    Sequence,
    Tuple,
    TypeVar,
    Union,
    cast,
)

from typing_extensions import Buffer

from ._handle import _ProxyHandle, _ProxyTimerHandle
from ._protocol import _BaseProtocolProxy, _proto_proxy, _proto_proxy_factory
from ._server import _ServerProxy
from ._task import Future, Task
from ._transport import _BaseTransportProxy, _make_transport_proxy

_R = TypeVar("_R")


# stable
class _HasFileno(Protocol):
    def fileno(self) -> int:
        ...


class _TaskFactory(Protocol):
    def __call__(
        self,
        __loop: asyncio.AbstractEventLoop,
        __factory: Coroutine[Any, Any, _R] | Generator[Any, None, _R],
    ) -> asyncio.Future[_R]:
        ...


_FileDescriptor = int  # stable
_FileDescriptorLike = Union[int, _HasFileno]  # stable

_Address = Union[Tuple[str, int], Tuple[str, int, int, int]]

_ExceptionContext = Dict[str, Any]
_ExceptionHandler = Callable[[asyncio.AbstractEventLoop, _ExceptionContext], Any]
_ProtocolT = TypeVar("_ProtocolT", bound=asyncio.BaseProtocol)
_ProtocolFactory = Callable[[], asyncio.BaseProtocol]
_SSLContext = Union[bool, ssl.SSLContext, None]
_TransProtPair = Tuple[asyncio.BaseTransport, asyncio.BaseProtocol]


def _get_fd(fd: _FileDescriptorLike) -> int:
    if isinstance(fd, int):
        return fd
    else:
        return fd.fileno()


class CheckKind(enum.Flag):
    TASKS = enum.auto()
    SIGNALS = enum.auto()
    SERVERS = enum.auto()
    TRANSPORTS = enum.auto()
    READERS = enum.auto()
    WRITERS = enum.auto()
    HANDLES = enum.auto()

    ALL = TASKS | SIGNALS | SERVERS | TRANSPORTS | READERS | WRITERS


class LoopProxy(asyncio.AbstractEventLoop):
    # allows fast check for loop proxy,
    # isinstance check is much slower
    _proxy_loop_marker = True

    def __init__(self, parent: asyncio.AbstractEventLoop) -> None:
        assert isinstance(parent, asyncio.AbstractEventLoop)
        self._parent = parent
        self._closed = False

        self._ready: set[_ProxyHandle] = set()
        self._timers: set[_ProxyTimerHandle] = set()
        self._readers: dict[int, asyncio.Handle] = {}
        self._writers: dict[int, asyncio.Handle] = {}
        self._tasks: weakref.WeakSet[Task[Any]] = weakref.WeakSet()
        self._transports: weakref.WeakSet[_BaseTransportProxy] = weakref.WeakSet()
        self._servers: weakref.WeakSet[_ServerProxy] = weakref.WeakSet()
        self._signals: dict[int, asyncio.Handle] = {}

        self._task_factory: _TaskFactory | None = None
        self._default_executor: Any = None
        self._executor_shutdown_called = False

        self._root_task: asyncio.Future[Any] | None = None
        self._time_offset: float = 0.0

    def __repr__(self) -> str:
        running = self.is_running()
        closed = self.is_closed()
        debug = self.get_debug()
        return f"<LoopProxy running={running} closed={closed} debug={debug}>"

    # properties
    @property
    def slow_callback_duration(self) -> float:
        return self._parent.slow_callback_duration

    @slow_callback_duration.setter
    def slow_callback_duration(self, value: float) -> None:
        self._parent.slow_callback_duration = value

    # Proxy-specific API

    def get_parent_loop(self) -> asyncio.AbstractEventLoop:
        return self._parent

    def advance_time(self, offset: float) -> None:
        self._time_offset += offset
        parent_loop = self._parent
        for timer in self._timers:
            parent = cast(asyncio.TimerHandle, timer._parent)
            if parent is None or parent.cancelled():
                # cancelled or executed
                continue
            # Reschedule the parent proxy timer by shifting it BACK to offset seconds
            parent.cancel()
            parent = parent_loop.call_at(parent.when() - offset, timer._run)
            timer._set_parent(parent)

    async def check_and_shutdown(self, kind: CheckKind = CheckKind.ALL) -> None:
        for task in list(self._tasks):
            if task is self._root_task:  # type: ignore
                continue
            if task.done():
                continue
            if kind & CheckKind.TASKS:
                warnings.warn(
                    f"Unfinished task {task!r}", ResourceWarning, stacklevel=2
                )
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

        for sig in list(self._signals):
            if kind & CheckKind.SIGNALS:
                warnings.warn(
                    f"Unregistered signal {sig}", ResourceWarning, stacklevel=2
                )
            self.remove_signal_handler(sig)
        self._signals.clear()

        for server in list(self._servers):
            if not server.is_serving():
                continue
            if kind & CheckKind.SERVERS:
                warnings.warn(
                    f"Unclosed server {server!r}", ResourceWarning, stacklevel=2
                )
            server.close()
            await server.wait_closed()
        self._servers.clear()

        for transport in list(self._transports):
            if not transport.is_closing():
                # close if not closing yet
                if kind & CheckKind.TRANSPORTS:
                    warnings.warn(
                        f"Unclosed transport {transport!r}",
                        ResourceWarning,
                        stacklevel=2,
                    )
                if isinstance(transport, asyncio.WriteTransport):
                    transport.abort()
                else:
                    transport.close()
            original = transport._orig
            if original is not None:
                proto = original.get_protocol()
                if proto is not None:
                    if isinstance(proto, _BaseProtocolProxy):
                        # On Windows it can be SSLProtocol for some reason???
                        await proto.wait_closed
        self._transports.clear()

        for fd, handle in list(self._readers.items()):
            if kind & CheckKind.READERS:
                warnings.warn(
                    f"Unfinished reader {fd} -> {handle!r}",
                    ResourceWarning,
                    stacklevel=2,
                )
            self.remove_reader(fd)
        self._readers.clear()

        for fd, handle in list(self._writers.items()):
            if kind & CheckKind.WRITERS:
                warnings.warn(
                    f"Unfinished writer {fd} -> {handle!r}",
                    ResourceWarning,
                    stacklevel=2,
                )
            self.remove_writer(fd)
        self._writers.clear()

        for handle in list(self._ready):
            # Don't warn about unfinished handles,
            # asyncio loop doesn't treat it as a resource leak
            handle.cancel()
        self._ready.clear()

        for timer in list(self._timers):
            # Don't warn about unfinished timers,
            # asyncio loop doesn't treat it as a resource leak
            timer.cancel()
        self._timers.clear()

    # Running and stopping the event loop.

    def run_forever(self) -> None:
        self._check_closed()
        self._parent.run_forever()

    def run_until_complete(
        self, coro_or_future: Awaitable[_R] | Generator[Any, None, _R]
    ) -> _R:
        async def main() -> _R:
            new_task = not asyncio.isfuture(coro_or_future)
            future = asyncio.ensure_future(coro_or_future, loop=self)  # type:ignore
            if new_task:
                # An exception is raised if the future didn't complete,
                # so there
                # is no need to log the "destroy pending task" message
                future._log_destroy_pending = False  # type: ignore[attr-defined]
            waiter = self._parent.create_future()
            self._chain_future(waiter, future)
            if new_task:
                self._root_task = future
            try:
                ret = await waiter
            finally:
                self._root_task = None
            return cast(_R, ret)

        return self._parent.run_until_complete(main())

    def stop(self) -> None:
        return self._parent.stop()

    def is_running(self) -> bool:
        return self._parent.is_running()

    def is_closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self.is_running():
            raise RuntimeError("Cannot close a running event loop")
        self._closed = True
        self._executor_shutdown_called = True
        executor = self._default_executor
        if executor is not None:
            warnings.warn(
                "Please call 'await proxy.shutdown_default_executor() explicitly "
                "before 'loop.close()' call.",
                RuntimeWarning,
            )
            self._default_executor = None
            executor.shutdown(wait=False)

    async def shutdown_asyncgens(self) -> None:
        warnings.warn(
            "Only the original loop can shutdown async generators", RuntimeWarning
        )
        return

    async def shutdown_default_executor(self) -> None:
        self._executor_shutdown_called = True
        if self._default_executor is None:
            return
        future = self.create_future()
        thread = threading.Thread(target=self._do_shutdown, args=(future,))
        thread.start()
        try:
            await future
        finally:
            thread.join()

    def _do_shutdown(self, future: Future[Any]) -> None:
        try:
            self._default_executor.shutdown(wait=True)
            self.call_soon_threadsafe(future.set_result, None)
        except Exception as ex:
            self.call_soon_threadsafe(future.set_exception, ex)
        finally:
            self._default_executor = None

    # Methods scheduling callbacks.  All these return Handles.

    def call_soon(
        self,
        callback: Callable[..., Any],
        *args: Any,
        context: contextvars.Context | None = None,
    ) -> asyncio.Handle:
        self._check_closed()
        handle = _ProxyHandle(callback, args, self, context)
        parent_handle = self._parent.call_soon(handle._run)
        handle._set_parent(parent_handle)
        if handle._source_traceback:
            del handle._source_traceback[-1]
        return handle

    def call_later(
        self,
        delay: float,
        callback: Callable[..., Any],
        *args: Any,
        context: contextvars.Context | None = None,
    ) -> asyncio.TimerHandle:
        self._check_closed()
        timer = _ProxyTimerHandle(self.time() + delay, callback, args, self, context)
        parent_timer = self._parent.call_later(delay, timer._run)
        timer._set_parent(parent_timer)
        if timer._source_traceback:
            del timer._source_traceback[-1]
        return timer

    def call_at(
        self,
        when: float,
        callback: Callable[..., Any],
        *args: Any,
        context: contextvars.Context | None = None,
    ) -> asyncio.TimerHandle:
        self._check_closed()
        timer = _ProxyTimerHandle(when, callback, args, self, context)
        parent_timer = self._parent.call_at(when, timer._run)
        timer._set_parent(parent_timer)
        if timer._source_traceback:
            del timer._source_traceback[-1]
        return timer

    def time(self) -> float:
        return self._parent.time() + self._time_offset

    def create_future(self) -> asyncio.Future[Any]:
        return Future(loop=self)  # type: ignore[return-value, misc]

    # Method scheduling a coroutine object: create a task.

    def create_task(
        self,
        coro: Generator[Any, None, _R] | Coroutine[Any, Any, _R],
        *,
        name: str | None = None,
        context: Context | None = None,
    ) -> asyncio.Task[_R]:
        self._check_closed()
        if self._task_factory is None:
            task = Task(coro, loop=self, name=name)
            if task._source_traceback:
                del task._source_traceback[-1]
        else:
            task = self._task_factory(self, coro)  # type: ignore
            if name is not None:
                try:
                    set_name = task.set_name
                except AttributeError:
                    pass  # Python < 3.9 has no task name support
                else:
                    set_name(name)
        self._tasks.add(task)
        return task  # type: ignore[return-value]

    # Methods for interacting with threads.

    def call_soon_threadsafe(
        self,
        callback: Callable[..., Any],
        *args: Any,
        context: contextvars.Context | None = None,
    ) -> asyncio.Handle:
        self._check_closed()
        handle = _ProxyHandle(callback, args, self, context)
        parent_handle = self._parent.call_soon_threadsafe(handle._run)
        handle._set_parent(parent_handle)
        if handle._source_traceback:
            del handle._source_traceback[-1]
        return handle

    def run_in_executor(
        self, executor: Any, func: Callable[..., _R], *args: Any
    ) -> asyncio.Future[_R]:
        self._check_closed()
        if executor is None:
            executor = self._default_executor
            # Only check when the default executor is being used
            self._check_default_executor()
            if executor is None:
                executor = concurrent.futures.ThreadPoolExecutor(
                    thread_name_prefix="aioloop-proxy"
                )
                self._default_executor = executor
        parent_fut = self._parent.run_in_executor(executor, func, *args)
        fut = self.create_future()
        self._chain_future(fut, parent_fut)
        return fut

    def set_default_executor(self, executor: Any) -> None:
        if not isinstance(executor, concurrent.futures.ThreadPoolExecutor):
            raise TypeError("executor must be ThreadPoolExecutor instance")
        self._default_executor = executor

    # Network I/O methods returning Futures.

    async def getaddrinfo(  # type: ignore[override]
        self, host: str | None, port: int | str | None, **kwargs: Any
    ) -> list[tuple[int, int, int, str, tuple[str, int] | tuple[str, int, int, int]]]:
        self._check_closed()
        return await self._wrap_async(
            self._parent.getaddrinfo(  # type: ignore[arg-type]
                host,
                port,
                **kwargs,
            )
        )

    async def getnameinfo(self, sockaddr: _Address, flags: int = 0) -> tuple[str, str]:
        self._check_closed()
        return await self._wrap_async(self._parent.getnameinfo(sockaddr, flags))

    async def create_connection(  # type: ignore[override]
        self,
        protocol_factory: Callable[[], _ProtocolT],
        host: str | None = None,
        port: int | None = None,
        **kwargs: Any,
    ) -> tuple[asyncio.BaseTransport, _ProtocolT]:
        self._check_closed()
        _, proto = await self._wrap_async(
            self._parent.create_connection(
                _proto_proxy_factory(protocol_factory, self),
                host,  # type: ignore[arg-type]
                port,  # type: ignore[arg-type]
                **kwargs,
            )
        )
        transp = proto.transport
        assert transp is not None
        self._transports.add(transp)
        return cast(asyncio.BaseTransport, transp), cast(_ProtocolT, proto.protocol)

    async def create_server(  # type: ignore[override]
        self,
        protocol_factory: Callable[[], _ProtocolT],
        host: str | Sequence[str] | None = None,
        port: int | None = None,
        **kwargs: Any,
    ) -> asyncio.AbstractServer:
        self._check_closed()
        server = await self._wrap_async(
            self._parent.create_server(
                _proto_proxy_factory(protocol_factory, self),
                host,
                port,  # type: ignore[arg-type]
                **kwargs,
            )
        )
        server_proxy = _ServerProxy(server, self)
        self._servers.add(server_proxy)
        return server_proxy

    async def sendfile(
        self,
        transport: asyncio.BaseTransport,
        file: IO[bytes],
        offset: int = 0,
        count: int | None = None,
        *,
        fallback: bool = True,
    ) -> int:
        self._check_closed()
        sent_count = await self._wrap_async(
            self._parent.sendfile(
                cast(
                    asyncio.WriteTransport, cast(_BaseTransportProxy, transport)._orig
                ),
                file,
                offset,
                count,
                fallback=fallback,
            )
        )
        return sent_count

    if sys.version_info >= (3, 11):

        async def start_tls(
            self,
            transport: asyncio.BaseTransport,
            protocol: asyncio.BaseProtocol,
            sslcontext: ssl.SSLContext,
            *,
            server_side: bool = False,
            server_hostname: str | None = None,
            ssl_handshake_timeout: float | None = None,
            ssl_shutdown_timeout: float | None = None,
        ) -> asyncio.Transport:
            self._check_closed()
            proto = _proto_proxy(protocol, self)
            tr = await self._wrap_async(
                self._parent.start_tls(
                    cast(
                        asyncio.WriteTransport,
                        cast(_BaseTransportProxy, transport)._orig,
                    ),
                    proto,
                    sslcontext,
                    server_side=server_side,
                    server_hostname=server_hostname,
                    ssl_handshake_timeout=ssl_handshake_timeout,
                    ssl_shutdown_timeout=ssl_shutdown_timeout,
                )
            )
            transp = _make_transport_proxy(tr, self)
            proto.transport = transp
            self._transports.add(transp)
            return cast(asyncio.Transport, transp)

    else:

        async def start_tls(
            self,
            transport: asyncio.BaseTransport,
            protocol: asyncio.BaseProtocol,
            sslcontext: ssl.SSLContext,
            *,
            server_side: bool = False,
            server_hostname: str | None = None,
            ssl_handshake_timeout: float | None = None,
        ) -> asyncio.Transport:
            self._check_closed()
            proto = _proto_proxy(protocol, self)
            tr = await self._wrap_async(
                self._parent.start_tls(
                    cast(
                        asyncio.WriteTransport,
                        cast(_BaseTransportProxy, transport)._orig,
                    ),
                    proto,
                    sslcontext,
                    server_side=server_side,
                    server_hostname=server_hostname,
                    ssl_handshake_timeout=ssl_handshake_timeout,
                )
            )
            transp = _make_transport_proxy(tr, self)
            proto.transport = transp
            self._transports.add(transp)
            return cast(asyncio.Transport, transp)

    if sys.version_info >= (3, 11):

        async def create_unix_connection(
            self,
            protocol_factory: Callable[[], _ProtocolT],
            path: str | None = None,
            *,
            ssl: bool | ssl.SSLContext | None = None,
            sock: socket.socket | None = None,
            server_hostname: str | None = None,
            ssl_handshake_timeout: float | None = None,
            ssl_shutdown_timeout: float | None = None,
        ) -> tuple[asyncio.Transport, _ProtocolT]:
            self._check_closed()
            _, proto = await self._wrap_async(
                self._parent.create_unix_connection(
                    _proto_proxy_factory(protocol_factory, self),
                    path,
                    ssl=ssl,
                    sock=sock,
                    server_hostname=server_hostname,
                    ssl_handshake_timeout=ssl_handshake_timeout,
                    ssl_shutdown_timeout=ssl_shutdown_timeout,
                )
            )
            transp = proto.transport
            assert transp is not None
            self._transports.add(transp)
            return cast(asyncio.Transport, transp), cast(_ProtocolT, proto.protocol)

    else:

        async def create_unix_connection(
            self,
            protocol_factory: Callable[[], _ProtocolT],
            path: str | None = None,
            *,
            ssl: bool | ssl.SSLContext | None = None,
            sock: socket.socket | None = None,
            server_hostname: str | None = None,
            ssl_handshake_timeout: float | None = None,
        ) -> tuple[asyncio.Transport, _ProtocolT]:
            self._check_closed()
            _, proto = await self._wrap_async(
                self._parent.create_unix_connection(
                    _proto_proxy_factory(protocol_factory, self),
                    path,
                    ssl=ssl,
                    sock=sock,
                    server_hostname=server_hostname,
                    ssl_handshake_timeout=ssl_handshake_timeout,
                )
            )
            transp = proto.transport
            assert transp is not None
            self._transports.add(transp)
            return cast(asyncio.Transport, transp), cast(_ProtocolT, proto.protocol)

    async def create_unix_server(  # type: ignore[override]
        self,
        protocol_factory: _ProtocolFactory,
        path: str | None = None,
        **kwargs: Any,
    ) -> asyncio.AbstractServer:
        self._check_closed()
        server = await self._wrap_async(
            self._parent.create_unix_server(
                _proto_proxy_factory(protocol_factory, self),
                path,
                **kwargs,
            )
        )
        server_proxy = _ServerProxy(server, self)
        self._servers.add(server_proxy)
        return server_proxy

    if sys.version_info >= (3, 11):

        async def connect_accepted_socket(
            self,
            protocol_factory: _ProtocolFactory,
            sock: socket.socket,
            *,
            ssl: bool | ssl.SSLContext | None = None,
            ssl_handshake_timeout: float | None = None,
            ssl_shutdown_timeout: float | None = None,
        ) -> tuple[asyncio.Transport, _ProtocolT]:
            self._check_closed()
            _, proto = await self._wrap_async(
                self._parent.connect_accepted_socket(
                    _proto_proxy_factory(protocol_factory, self),
                    sock,
                    ssl=ssl,
                    ssl_handshake_timeout=ssl_handshake_timeout,
                    ssl_shutdown_timeout=ssl_shutdown_timeout,
                )
            )
            transp = proto.transport
            assert transp is not None
            self._transports.add(transp)
            return cast(asyncio.Transport, transp), cast(_ProtocolT, proto.protocol)

    elif sys.version_info >= (3, 10):

        async def connect_accepted_socket(
            self,
            protocol_factory: _ProtocolFactory,
            sock: socket.socket,
            *,
            ssl: bool | ssl.SSLContext | None = None,
            ssl_handshake_timeout: float | None = None,
        ) -> tuple[asyncio.Transport, _ProtocolT]:
            self._check_closed()
            _, proto = await self._wrap_async(
                self._parent.connect_accepted_socket(
                    _proto_proxy_factory(protocol_factory, self),
                    sock,
                    ssl=ssl,
                    ssl_handshake_timeout=ssl_handshake_timeout,
                )
            )
            transp = proto.transport
            assert transp is not None
            self._transports.add(transp)
            return cast(asyncio.Transport, transp), cast(_ProtocolT, proto.protocol)

    async def create_datagram_endpoint(
        self,
        protocol_factory: Callable[[], _ProtocolT],
        local_addr: tuple[str, int] | str | None = None,
        remote_addr: tuple[str, int] | str | None = None,
        *,
        family: int = 0,
        proto: int = 0,
        flags: int = 0,
        reuse_address: bool | None = None,
        reuse_port: bool | None = None,
        allow_broadcast: bool | None = None,
        sock: socket.socket | None = None,
    ) -> tuple[asyncio.DatagramTransport, _ProtocolT]:
        self._check_closed()
        _, protocol = await self._wrap_async(
            self._parent.create_datagram_endpoint(
                _proto_proxy_factory(protocol_factory, self),
                local_addr,
                remote_addr,
                family=family,
                proto=proto,
                flags=flags,
                # note: reuse_address arg is not supported by asyncio.BaseEventLoop
                reuse_port=reuse_port,
                allow_broadcast=allow_broadcast,
                sock=sock,
            )
        )
        transp = protocol.transport
        assert transp is not None
        self._transports.add(transp)
        return cast(asyncio.DatagramTransport, transp), cast(
            _ProtocolT, protocol.protocol
        )

    # Pipes and subprocesses.

    async def connect_read_pipe(
        self, protocol_factory: Callable[[], _ProtocolT], pipe: Any
    ) -> tuple[asyncio.ReadTransport, _ProtocolT]:
        self._check_closed()
        _, proto = await self._wrap_async(
            self._parent.connect_read_pipe(
                _proto_proxy_factory(protocol_factory, self), pipe
            )
        )
        transp = proto.transport
        assert transp is not None
        self._transports.add(transp)
        return transp, proto.protocol  # type: ignore[return-value]

    async def connect_write_pipe(
        self, protocol_factory: Callable[[], _ProtocolT], pipe: Any
    ) -> tuple[asyncio.WriteTransport, _ProtocolT]:
        self._check_closed()
        _, proto = await self._wrap_async(
            self._parent.connect_write_pipe(
                _proto_proxy_factory(protocol_factory, self), pipe
            )
        )
        transp = proto.transport
        assert transp is not None
        self._transports.add(transp)
        return transp, proto.protocol  # type: ignore[return-value]

    async def subprocess_shell(
        self,
        protocol_factory: Callable[[], _ProtocolT],
        cmd: bytes | str,
        **kwargs: Any,
    ) -> tuple[asyncio.SubprocessTransport, _ProtocolT]:
        self._check_closed()
        _, proto = await self._wrap_async(
            self._parent.subprocess_shell(
                _proto_proxy_factory(protocol_factory, self), cmd, **kwargs
            )
        )
        transp = proto.transport
        assert transp is not None
        self._transports.add(transp)
        return transp, proto.protocol  # type: ignore[return-value]

    async def subprocess_exec(
        self, protocol_factory: Callable[[], _ProtocolT], *args: Any, **kwargs: Any
    ) -> tuple[asyncio.SubprocessTransport, _ProtocolT]:
        self._check_closed()
        _, proto = await self._wrap_async(
            self._parent.subprocess_exec(
                _proto_proxy_factory(protocol_factory, self), *args, **kwargs
            )
        )
        transp = proto.transport
        assert transp is not None
        self._transports.add(transp)
        return transp, proto.protocol  # type: ignore[return-value]

    # Ready-based callback registration methods.
    # The add_*() methods return None.
    # The remove_*() methods return True if something was removed,
    # False if there was nothing to delete.

    def add_reader(
        self, fd: _FileDescriptorLike, callback: Callable[..., Any], *args: Any
    ) -> None:
        self._check_closed()
        handle = asyncio.Handle(callback, args, self)
        self._parent.add_reader(fd, self._wrap_cb, handle._run)
        self._readers[_get_fd(fd)] = handle

    def remove_reader(self, fd: _FileDescriptorLike) -> bool:
        if self.is_closed():
            return False
        self._parent.remove_reader(fd)
        handle = self._readers.pop(_get_fd(fd), None)
        if handle is not None:
            handle.cancel()
            return True
        else:
            return False

    def add_writer(
        self, fd: _FileDescriptorLike, callback: Callable[..., Any], *args: Any
    ) -> None:
        self._check_closed()
        handle = asyncio.Handle(callback, args, self)
        self._parent.add_writer(_get_fd(fd), self._wrap_cb, handle._run)
        self._writers[_get_fd(fd)] = handle

    def remove_writer(self, fd: _FileDescriptorLike) -> bool:
        if self.is_closed():
            return False
        self._parent.remove_writer(fd)
        handle = self._writers.pop(_get_fd(fd), None)
        if handle is not None:
            handle.cancel()
            return True
        else:
            return False

    # Completion based I/O methods returning Futures.

    async def sock_recv(self, sock: socket.socket, nbytes: int) -> bytes:
        self._check_closed()
        return await self._wrap_async(self._parent.sock_recv(sock, nbytes))

    async def sock_recv_into(self, sock: socket.socket, buf: Buffer) -> int:
        self._check_closed()
        return await self._wrap_async(self._parent.sock_recv_into(sock, buf))

    async def sock_sendall(self, sock: socket.socket, data: Buffer) -> None:
        self._check_closed()
        return await self._wrap_async(self._parent.sock_sendall(sock, data))

    async def sock_connect(
        self, sock: socket.socket, address: tuple[Any, ...] | str | Buffer
    ) -> None:
        self._check_closed()
        return await self._wrap_async(self._parent.sock_connect(sock, address))

    async def sock_accept(self, sock: socket.socket) -> tuple[socket.socket, Any]:
        self._check_closed()
        return await self._wrap_async(self._parent.sock_accept(sock))

    async def sock_sendfile(
        self,
        sock: socket.socket,
        file: IO[bytes],
        offset: int = 0,
        count: int | None = None,
        *,
        fallback: bool | None = None,
    ) -> int:
        self._check_closed()
        return await self._wrap_async(
            self._parent.sock_sendfile(sock, file, offset, count, fallback=fallback)
        )

    if sys.version_info >= (3, 11):

        async def sock_recvfrom(
            self, sock: socket.socket, bufsize: int
        ) -> tuple[bytes, Any]:
            self._check_closed()
            return await self._wrap_async(self._parent.sock_recvfrom(sock, bufsize))

        async def sock_recvfrom_into(
            self, sock: socket.socket, buf: Buffer, nbytes: int = 0
        ) -> tuple[int, Any]:
            self._check_closed()
            return await self._wrap_async(
                self._parent.sock_recvfrom_into(sock, buf, nbytes)
            )

        async def sock_sendto(
            self, sock: socket.socket, data: Buffer, address: Any
        ) -> int:
            self._check_closed()
            return await self._wrap_async(self._parent.sock_sendto(sock, data, address))

    # Signal handling.

    def add_signal_handler(
        self, sig: int, callback: Callable[..., Any], *args: Any
    ) -> None:
        self._check_closed()
        handle = asyncio.Handle(callback, args, self)
        self._parent.add_signal_handler(sig, self._wrap_cb, handle._run)
        self._signals[sig] = handle

    def remove_signal_handler(self, sig: int) -> bool:
        handler = self._signals.pop(sig, None)
        self._parent.remove_signal_handler(sig)
        return handler is not None

    # Task factory.

    def set_task_factory(
        self,
        factory: _TaskFactory | None,
    ) -> None:
        if factory is not None and not callable(factory):
            raise TypeError("task factory must be a callable or None")
        self._task_factory = factory

    def get_task_factory(
        self,
    ) -> _TaskFactory | None:
        return self._task_factory

    # Error handlers.

    def get_exception_handler(self) -> _ExceptionHandler | None:
        return self._parent.get_exception_handler()

    def set_exception_handler(self, handler: _ExceptionHandler | None) -> None:
        return self._parent.set_exception_handler(handler)

    def default_exception_handler(self, context: _ExceptionContext) -> None:
        return self._parent.default_exception_handler(context)

    def call_exception_handler(self, context: _ExceptionContext) -> None:
        return self._parent.call_exception_handler(context)

    # Debug flag management.

    def get_debug(self) -> bool:
        return self._parent.get_debug()

    def set_debug(self, enabled: bool) -> None:
        self._parent.set_debug(enabled)

    # Inherited

    def _timer_handle_cancelled(self, handle: asyncio.TimerHandle) -> None:
        # Nothing to do, _ProxyTimerHandle.cancel()
        # already cancelled the parent timer
        pass

    # Implementation details

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError("Event loop is closed")

    def _check_default_executor(self) -> None:
        if self._executor_shutdown_called:
            raise RuntimeError("Executor shutdown has been called")

    def _wrap_cb(self, __func: Callable[..., _R], *args: Any, **kwargs: Any) -> _R:
        # Private API calls are OK here
        loop = asyncio._get_running_loop()
        asyncio._set_running_loop(self)
        try:
            return __func(*args, **kwargs)
        finally:
            asyncio._set_running_loop(loop)

    def _wrap_async(
        self, coro_or_future: Awaitable[_R] | Coroutine[Any, Any, _R]
    ) -> asyncio.Future[_R]:
        # Private API calls are OK here
        loop = asyncio._get_running_loop()
        assert loop is None or loop is self
        fut = self.create_future()
        inner_fut = asyncio.ensure_future(coro_or_future, loop=self._parent)
        self._chain_future(fut, inner_fut)
        return fut

    def _chain_future(
        self,
        target: asyncio.Future[_R],
        source: asyncio.Future[_R],
    ) -> None:
        def _call_check_cancel(target: asyncio.Future[_R]) -> None:
            if target.cancelled():
                source.cancel()

        def _call_set_state(source: asyncio.Future[_R]) -> None:
            if source.cancelled():
                target.cancel()
                return
            exc = source.exception()
            if exc is not None:
                target.set_exception(exc)
            else:
                res = source.result()
                target.set_result(res)

        target.add_done_callback(_call_check_cancel)
        source.add_done_callback(_call_set_state)

import asyncio
import unittest
from unittest.mock import Mock

import aioloop_proxy
from aioloop_proxy._protocol import _proto_proxy

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


class TestProtocol(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test_repr(self):
        proto = Mock(spec=asyncio.Protocol)
        proxy = _proto_proxy(proto, self.loop)
        self.assertEqual(repr(proto), repr(proxy))

    def test__make_proto_proxy_unknown(self):
        with self.assertRaisesRegex(RuntimeError, "Cannot find protocol proxy"):
            _proto_proxy(object(), self.loop)

    def test__make_proto_proxy_universal(self):
        class Proto(asyncio.Protocol, asyncio.BufferedProtocol):
            pass

        proto = _proto_proxy(Proto(), self.loop)
        self.assertIsInstance(proto, asyncio.Protocol)
        self.assertIsInstance(proto, asyncio.BufferedProtocol)

    def test_pause_writing(self):
        proto = Mock(spec=asyncio.BaseProtocol)
        proxy = _proto_proxy(proto, self.loop)
        proxy.pause_writing()
        proto.pause_writing.assert_called_once_with()

    def test_resume_writing(self):
        proto = Mock(spec=asyncio.BaseProtocol)
        proxy = _proto_proxy(proto, self.loop)
        proxy.resume_writing()
        proto.resume_writing.assert_called_once_with()

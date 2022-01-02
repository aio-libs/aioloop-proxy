import asyncio
import sys
import unittest
from unittest.mock import Mock

import aioloop_proxy
from aioloop_proxy._protocol import _proto_proxy
from aioloop_proxy._transport import _make_transport_proxy

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


class TestTransport(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        if not self.loop.is_closed():
            self.loop.run_until_complete(self.loop.check_and_shutdown())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

    def test__make_transport_proxy_unknown(self):
        with self.assertRaisesRegex(RuntimeError, "Cannot find transport proxy"):
            _make_transport_proxy(object(), self.loop)

    def test_get_protocol(self):
        orig = Mock(spec=asyncio.BaseTransport)
        prot = Mock(spec=asyncio.BaseProtocol)
        orig.get_protocol.return_value = _proto_proxy(prot, self.loop)
        transp = _make_transport_proxy(orig, self.loop)
        self.assertIs(transp.get_protocol(), prot)

    @unittest.skipIf(sys.version_info < (3, 8), "call_args are buggy in Python 3.7")
    def test_set_protocol(self):
        orig = Mock(spec=asyncio.BaseTransport)
        prot = Mock(spec=asyncio.BaseProtocol)
        transp = _make_transport_proxy(orig, self.loop)
        transp.set_protocol(prot)
        kall = orig.set_protocol.call_args
        self.assertEqual({}, kall.kwargs)
        self.assertEqual(1, len(kall.args))
        self.assertIs(prot, kall.args[0].protocol)

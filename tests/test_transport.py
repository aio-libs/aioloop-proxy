import asyncio
import unittest
from unittest.mock import Mock, sentinel

import aioloop_proxy
from aioloop_proxy._transport import _make_transport_proxy

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


class TestTransport(unittest.TestCase):
    def setUp(self):
        self.loop = aioloop_proxy.LoopProxy(_loop)

    def tearDown(self):
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        self.loop.check_resouces(strict=True)
        self.loop.close()

    def test__make_transport_proxy_unknown(self):
        with self.assertRaisesRegex(RuntimeError, "Cannot find transport proxy"):
            _make_transport_proxy(object(), self.loop)

    def test_get_protocol(self):
        orig = Mock(spec=asyncio.BaseTransport)
        orig.get_protocol.return_value = sentinel
        transp = _make_transport_proxy(orig, self.loop)
        self.assertIs(transp.get_protocol(), sentinel)

    def test_set_protocol(self):
        orig = Mock(spec=asyncio.BaseTransport)
        transp = _make_transport_proxy(orig, self.loop)
        transp.set_protocol(sentinel)
        orig.set_protocol.assert_called_once_with(sentinel)

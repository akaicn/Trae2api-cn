"""Tests for the Windows-safe uvicorn event-loop factory.

uvicorn's Windows default (``ProactorEventLoop``) permanently closes the
listening socket on a transient accept error such as ``WinError 64``, leaving
the process alive but the port dead.  ``src.asyncio_loop.loop_factory`` must
return the select-based loop for every mode except the CLI upstream, which
needs asyncio subprocesses that only the Proactor loop supports.
"""
import asyncio
import sys
import unittest
from unittest.mock import patch

from src import asyncio_loop


class LoopFactoryTests(unittest.TestCase):
    def test_selector_loop_factory_is_selector(self):
        loop = asyncio_loop.selector_loop_factory()
        try:
            self.assertIsInstance(loop, asyncio.SelectorEventLoop)
            self.assertNotIsInstance(loop, asyncio.ProactorEventLoop)
        finally:
            loop.close()

    def test_uses_proactor_only_for_cli_upstream(self):
        with patch.object(asyncio_loop, "_cli_upstream", return_value=True), patch.object(
            sys, "platform", "win32"
        ):
            self.assertTrue(asyncio_loop.uses_proactor())
        with patch.object(asyncio_loop, "_cli_upstream", return_value=False), patch.object(
            sys, "platform", "win32"
        ):
            self.assertFalse(asyncio_loop.uses_proactor())
        with patch.object(asyncio_loop, "_cli_upstream", return_value=True), patch.object(
            sys, "platform", "linux"
        ):
            self.assertFalse(asyncio_loop.uses_proactor())

    def test_loop_factory_prefers_selector_on_win32(self):
        with patch.object(sys, "platform", "win32"), patch.object(
            asyncio_loop, "_cli_upstream", return_value=False
        ):
            loop = asyncio_loop.loop_factory()
            try:
                self.assertIsInstance(loop, asyncio.SelectorEventLoop)
                self.assertNotIsInstance(loop, asyncio.ProactorEventLoop)
            finally:
                loop.close()

    def test_loop_factory_keeps_proactor_for_cli(self):
        with patch.object(sys, "platform", "win32"), patch.object(
            asyncio_loop, "_cli_upstream", return_value=True
        ):
            loop = asyncio_loop.loop_factory()
            try:
                self.assertIsInstance(loop, asyncio.ProactorEventLoop)
            finally:
                loop.close()

    def test_cli_upstream_reads_env(self):
        with patch.dict("os.environ", {"UPSTREAM_MODE": "cli", "TRAE_AUTH_SOURCE": ""}, clear=False):
            self.assertTrue(asyncio_loop._cli_upstream())
        with patch.dict(
            "os.environ", {"UPSTREAM_MODE": "ide", "TRAE_AUTH_SOURCE": "web-login"}, clear=False
        ):
            self.assertFalse(asyncio_loop._cli_upstream())


if __name__ == "__main__":
    unittest.main()

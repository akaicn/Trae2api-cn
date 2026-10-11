"""Event-loop selection for uvicorn on Windows.

uvicorn uses ``ProactorEventLoop`` (IOCP) for single-process runs on Windows.
That loop's accept handler **closes the listening socket and stops re-arming**
when ``accept`` reports a *transient* error such as ``WinError 64``
(``ERROR_NETNAME_DELETED``), which Windows raises when a peer aborts a
connection right as it is being accepted -- common with large request bodies
and clients that time out or disconnect mid-upload.

The result is the failure this project hit: the uvicorn process keeps running,
but the port silently stops listening, so every new request is refused while
``/v1/status`` health probes fail. ``SelectorEventLoop`` treats those same
errors as non-fatal and keeps serving, so we prefer it on Windows.

``ProactorEventLoop`` is still required for ``asyncio`` subprocesses
(``asyncio.create_subprocess_exec``), which only the Trae CLI upstream uses.
CLI mode therefore keeps the default Proactor loop.

Usage (uvicorn ``--loop`` accepts a zero-arg factory import string)::

    uvicorn src.main:app --loop src.asyncio_loop:loop_factory
"""
from __future__ import annotations

import asyncio
import os
import sys

__all__ = ["loop_factory", "selector_loop_factory", "uses_proactor"]


def _cli_upstream() -> bool:
    """Whether the Trae CLI upstream (needs asyncio subprocesses) is selected.

    ``.env`` is not loaded by the time uvicorn creates the loop, so read it
    here when the process environment does not already carry the values.
    """
    mode = os.environ.get("UPSTREAM_MODE")
    source = os.environ.get("TRAE_AUTH_SOURCE")
    if mode is None and source is None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass
        mode = os.environ.get("UPSTREAM_MODE")
        source = os.environ.get("TRAE_AUTH_SOURCE")
    mode = (mode or "").strip().lower()
    source = (source or "").strip().lower()
    return mode == "cli" or source == "cli"


def uses_proactor() -> bool:
    """Whether the selected upstream forces the Proactor loop on Windows."""

    return sys.platform == "win32" and _cli_upstream()


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Always use the select-based loop (safe default on Windows)."""

    return asyncio.SelectorEventLoop()


def loop_factory() -> asyncio.AbstractEventLoop:
    """Pick the Windows-safe loop, keeping Proactor only for CLI mode."""

    if sys.platform != "win32":
        return asyncio.SelectorEventLoop()
    if _cli_upstream():
        return asyncio.ProactorEventLoop()
    return asyncio.SelectorEventLoop()

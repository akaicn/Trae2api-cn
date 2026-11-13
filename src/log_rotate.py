"""Size-based rotation for the tray-captured relay log.

``logs/relay.log`` is not opened by this process: the tray launches uvicorn
with ``stdout=<append handle on relay.log>``, so the file is *already open*
while we run.  On Windows an open file cannot be renamed, so the usual
``RotatingFileHandler`` dance (rename then reopen) is impossible here.

Instead the rotation copies the current content to ``relay.log.1`` (shifting
older archives) and then truncates the live file in place.  Appends made
through the supervisor's handle always target the current end of file, so the
next write after the truncate lands at offset 0 - no null-filled hole.

Only the bytes that are written *between* the copy and the truncate can be
lost; that window is a few milliseconds on a 50 MB file.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_BACKUPS = 5
DEFAULT_INTERVAL_SECONDS = 60.0
_COPY_CHUNK = 1024 * 1024

_started = False
_lock = threading.Lock()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def default_log_path() -> Path:
    override = os.environ.get("RELAY_LOG_PATH")
    if override and str(override).strip():
        return Path(str(override).strip()).expanduser()
    return Path(__file__).resolve().parent.parent / "logs" / "relay.log"


def _stream_identity(stream) -> Optional[tuple[int, int]]:
    try:
        stat = os.fstat(stream.fileno())
    except Exception:
        return None
    return (stat.st_dev, stat.st_ino)


def _is_captured_stdout(path: Path) -> bool:
    """True when ``path`` is the very file this process writes stdout to.

    Rotation must never touch a log the supervisor is not capturing (for
    example a container that keeps stdout on the console).
    """

    for stream in (sys.stdout, sys.stderr):
        identity = _stream_identity(stream)
        if identity is None:
            continue
        try:
            stat = path.stat()
        except OSError:
            return False
        if (stat.st_dev, stat.st_ino) == identity:
            return True
    return False


def _archive_path(path: Path, index: int) -> Path:
    return path.with_name(f"{path.name}.{index}")


def _shift_archives(path: Path, backups: int) -> None:
    for index in range(backups - 1, 0, -1):
        source = _archive_path(path, index)
        if not source.exists():
            continue
        target = _archive_path(path, index + 1)
        try:
            os.replace(source, target)
        except OSError as exc:
            logger.warning("log rotation: cannot shift %s -> %s: %s", source, target, exc)


def _copy_to_archive(path: Path, index: int) -> None:
    target = _archive_path(path, index)
    with open(path, "rb") as source, open(target, "wb") as sink:
        shutil.copyfileobj(source, sink, _COPY_CHUNK)
        sink.flush()
        os.fsync(sink.fileno())


def _truncate(path: Path) -> None:
    with open(path, "r+b") as handle:
        handle.truncate(0)


def _reset_stream_position(stream) -> None:
    """Move ``stream``'s file position back to 0.

    The live log is this process's *inherited* stdout handle, not a handle we
    opened: the supervisor opened it in append mode and handed the fd to us.
    Windows appends only through the CRT's own append flag, which an inherited
    handle does not carry, so after a truncate the next write would land at the
    old end-of-file and leave a sparse gap of NULs.  Rewinding the shared file
    position keeps subsequent writes compact.
    """

    try:
        stream.flush()
    except Exception:
        pass
    try:
        os.lseek(stream.fileno(), 0, os.SEEK_SET)
    except Exception:
        pass


def rotate_now(path: Path, backups: int) -> bool:
    """Copy ``path`` to ``path.1`` and empty it.  Returns True on rotation."""

    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= 0:
        return False
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        if backups > 0:
            _shift_archives(path, backups)
            _copy_to_archive(path, 1)
        # Rewind before truncating: a write racing in between then lands at
        # offset 0 (harmless, the content is already archived) instead of at
        # the old end of file, which would punch a hole in the log.
        for stream in (sys.stdout, sys.stderr):
            _reset_stream_position(stream)
        _truncate(path)
    except OSError as exc:
        logger.warning("log rotation failed for %s: %s", path, exc)
        return False
    return True


def _loop(path: Path, max_bytes: int, backups: int, interval: float) -> None:
    if not _is_captured_stdout(path):
        logger.warning(
            "log rotation disabled: %s is not this process's stdout", path
        )
        return
    while True:
        time.sleep(interval)
        try:
            if path.stat().st_size < max_bytes:
                continue
            size = path.stat().st_size
            if rotate_now(path, backups):
                # This banner is the first thing written to the fresh file.
                print(
                    "===== log rotated %s (archived %d bytes -> %s) ====="
                    % (
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                        size,
                        _archive_path(path, 1).name if backups > 0 else "none",
                    ),
                    flush=True,
                )
        except Exception as exc:  # never let housekeeping kill the thread
            logger.warning("log rotation check failed: %s", exc)


def start_log_rotation() -> Optional[threading.Thread]:
    """Start the rotation thread once; returns None when it is disabled."""

    global _started
    with _lock:
        if _started:
            return None
        max_bytes = _env_int("RELAY_LOG_MAX_BYTES", DEFAULT_MAX_BYTES)
        backups = _env_int("RELAY_LOG_BACKUPS", DEFAULT_BACKUPS)
        interval = _env_float("RELAY_LOG_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)
        if max_bytes <= 0 or interval <= 0:
            logger.info("log rotation disabled by configuration")
            return None
        path = default_log_path()
        backups = max(0, backups)
        thread = threading.Thread(
            target=_loop,
            args=(path, max_bytes, backups, interval),
            name="relay-log-rotate",
            daemon=True,
        )
        thread.start()
        _started = True
    logger.info(
        "log rotation enabled path=%s max_bytes=%d backups=%d interval=%.0fs",
        path,
        max_bytes,
        backups,
        interval,
    )
    return thread

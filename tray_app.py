#!/usr/bin/env python3
"""tray_app.py - Trae2api-cn 托盘守护（Windows）

一键启动 relay 服务与网页授权助手，并在系统托盘提供右键菜单：
  打开控制台 / 启动·停止授权助手 / 重启服务 / 查看日志 / 退出

配合 启动服务.vbs（pythonw 无窗口）双击使用，也可直接：
  .venv\\Scripts\\python.exe tray_app.py

依赖：pystray、Pillow（见 requirements-tray.txt）。
"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
LOG_DIR = ROOT / "logs"
RELAY_LOG = LOG_DIR / "relay.log"
AUTH_LOG = LOG_DIR / "auth.log"
TRAY_LOG = LOG_DIR / "tray.log"

CONTROL_PORT = int(os.environ.get("TRAY_CONTROL_PORT", "8766"))
AUTH_LISTENER_PORT = int(os.environ.get("WEB_LOGIN_LISTENER_PORT", "8765"))

if os.name == "nt":
    # CREATE_NO_WINDOW: GUI 进程(pythonw)派生控制台子进程时不新建黑色控制台窗口
    CREATE_FLAGS = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
else:
    CREATE_FLAGS = 0


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with open(TRAY_LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def message_box(text: str, title: str = "Trae2api-cn", flags: int = 0x40) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        pass


def read_env() -> dict:
    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except Exception:
        return False


def _taskkill_tree(pid: int) -> None:
    """Force-kill a process and its children; ignore a process already gone.

    ``taskkill /T`` walks the live parent/child tree, so it also reaps the real
    interpreter behind the venv launcher stub as long as the stub is still
    alive.  Killing a dead PID is harmless (it just returns a non-zero code).
    """
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        else:
            os.kill(pid, 9)
    except Exception as exc:
        log(f"taskkill pid={pid} error: {exc}")


def _listening_pids(port: int) -> set:
    """Return PIDs that currently LISTEN on ``port`` (TCP, IPv4 + IPv6)."""
    pids: set = set()
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except Exception as exc:
        log(f"netstat failed: {exc}")
        return pids
    for line in out.splitlines():
        parts = line.split()
        # Proto  Local Address  Foreign Address  State  PID
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        if parts[1].rsplit(":", 1)[-1] != str(port):
            continue
        try:
            pids.add(int(parts[4]))
        except ValueError:
            pass
    return pids


def _pid_image_name(pid: int) -> str:
    """Return the image name (lowercase) for a PID, or '' when unknown."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        return ""
    if not out or out.upper().startswith("INFO"):
        return ""
    return out.split('","')[0].strip().strip('"').lower()


def _stray_relay_pids(port: int) -> set:
    """PIDs of ``python.exe`` processes serving *this relay's* port.

    Matches the relay command line (``uvicorn src.main:app ... --port N``) and
    confirms the ``--port`` argument equals ``port`` so the tray never touches
    a relay bound to a different port.  The ``Name='python.exe'`` filter keeps
    the query from matching the PowerShell host, and the tray itself runs as
    ``pythonw.exe``, so the tray is never returned here either.
    """
    if os.name != "nt":
        return set()
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*uvicorn*src.main*' } | "
        "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
    except Exception as exc:
        log(f"stray relay scan failed: {exc}")
        return set()
    wanted = str(port)
    pids: set = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        pid_text, cmdline = line.split("|", 1)
        tokens = cmdline.split()
        match = False
        for index, token in enumerate(tokens):
            if token == "--port" and index + 1 < len(tokens) and tokens[index + 1] == wanted:
                match = True
                break
            if token == f"--port={wanted}":
                match = True
                break
        if not match:
            continue
        try:
            pids.add(int(pid_text.strip()))
        except ValueError:
            pass
    return pids


def acquire_single_instance():
    """占一个本机控制端口作为单实例锁；已被占用时返回 None。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", CONTROL_PORT))
        sock.listen(1)
        return sock
    except OSError:
        try:
            sock.close()
        except Exception:
            pass
        return None


def make_icon_image(color: str = "#2f6fed", size: int = 64):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = max(2, size // 16)
    radius = size // 5
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad], radius=radius, fill=color
    )
    text = "T"
    font = None
    for name in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf"):
        try:
            font = ImageFont.truetype(name, int(size * 0.6))
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
        text,
        fill="white",
        font=font,
    )
    return img


def make_state_image(state: str):
    """优先用 assets/ 里的 logo 图标；缺失时回退到内置绘制的图标。

    state="ok" 用绿色运行版，state="off" 用红色停止版。
    """
    name = "trae2api-cn.png" if state == "ok" else "trae2api-cn-off.png"
    path = ASSETS / name
    if path.exists():
        try:
            from PIL import Image

            img = Image.open(path).convert("RGBA")
            if img.width > 64:
                img = img.resize((64, 64), Image.LANCZOS)
            return img
        except Exception as exc:
            log(f"load icon failed ({path}): {exc}")
    return make_icon_image("#22c55e" if state == "ok" else "#d24b4b")


class Manager:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.auth_proc: subprocess.Popen | None = None
        self._relay_pids: set = set()
        self._relay_fh = None
        self._auth_fh = None

    # ---- helpers -------------------------------------------------
    def _python(self) -> str:
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe":
            sibling = exe.with_name("python.exe")
            if sibling.exists():
                exe = sibling
        return str(exe)

    def _open_log(self, path: Path, title: str):
        LOG_DIR.mkdir(exist_ok=True)
        fh = open(path, "a", encoding="utf-8", errors="ignore")
        fh.write(f"\n===== {title} {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        fh.flush()
        return fh

    @staticmethod
    def _kill(proc: subprocess.Popen | None) -> None:
        """Stop a child process we spawned.

        On Windows ``.venv\\Scripts\\python.exe`` is a launcher stub that spawns
        the real interpreter as a *child*, so the process we must stop is not
        ``proc.pid`` itself.  ``taskkill /T`` walks the live tree and reaps the
        child too.  It is intentionally called even when the stub has already
        exited: the tree may still hold the orphaned interpreter, while killing
        an already-dead PID is a harmless no-op.
        """
        if not proc:
            return
        _taskkill_tree(proc.pid)
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _relay_process_alive(self) -> bool:
        """Whether this tray's relay launcher process is still running."""
        return bool(self.proc and self.proc.poll() is None)

    def _owned_or_stray_relay_pids(self) -> set:
        """PIDs that currently serve this relay: tracked child + stray matches.

        The tracked ``Popen`` PID is the venv launcher stub; the real uvicorn
        interpreter is its child and owns the listening socket, so a port scan
        is the authoritative way to find it.  Command-line matching then also
        catches orphans from a previous tray session that no longer have a live
        parent.
        """
        pids = set(_listening_pids(self.port))
        pids |= _stray_relay_pids(self.port)
        pids |= set(self._relay_pids)
        if self.proc and self.proc.poll() is None:
            pids.add(self.proc.pid)
        # Never target the tray's own process tree or the console we run in.
        pids.discard(os.getpid())
        return {pid for pid in pids if pid}

    def _free_port(self, timeout: float = 15.0) -> bool:
        """Ensure nothing is listening on ``self.port``; True when it is free.

        This is the guard that prevents the Windows ``SO_REUSEADDR`` failure
        mode: uvicorn sets ``SO_REUSEADDR`` unconditionally, so a lingering
        orphan plus a fresh bind silently create two listeners on one port and
        the asyncio accept loop dies with ``WinError 64`` without ever
        re-arming.  Kill every candidate first, then wait for the port to be
        released before any new bind.
        """
        deadline = time.time() + timeout
        killed: set = set()
        while True:
            listeners = _listening_pids(self.port)
            for pid in listeners:
                if pid == os.getpid():
                    continue
                name = _pid_image_name(pid)
                if name and "python" not in name:
                    # A non-Python program owns the port; do not touch it,
                    # just report the conflict so the caller can surface it.
                    log(f"port {self.port} held by foreign pid={pid} image={name}")
                    return False
                if pid not in killed:
                    log(f"releasing port {self.port}: taskkill stray pid={pid}")
                    _taskkill_tree(pid)
                    killed.add(pid)
            if not _listening_pids(self.port):
                return True
            if time.time() >= deadline:
                log(f"port {self.port} still held after {timeout:.0f}s")
                return not _listening_pids(self.port)
            time.sleep(0.5)

    # ---- relay ---------------------------------------------------
    def relay_up(self) -> bool:
        return http_ok(f"http://127.0.0.1:{self.port}/v1/status")

    def start_relay(self) -> bool:
        if self._relay_process_alive() and self.relay_up():
            return True
        # A dead/duplicate relay (or an orphan from an earlier session) must be
        # fully removed and the port released before binding again.
        if not self._free_port():
            return False
        self._relay_fh = self._open_log(RELAY_LOG, "relay start")
        self.proc = subprocess.Popen(
            [
                self._python(), "-m", "uvicorn", "src.main:app",
                "--host", self.host, "--port", str(self.port),
            ],
            cwd=str(ROOT),
            stdout=self._relay_fh,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_FLAGS,
        )
        self._relay_pids = {self.proc.pid}
        log(f"relay started pid={self.proc.pid}")
        return True

    def stop_relay(self) -> None:
        # Stop the tracked launcher/stub tree...
        self._kill(self.proc)
        # ...then any interpreter still holding the port or matching our
        # command line, which is where a venv orphan would live.
        for pid in self._owned_or_stray_relay_pids():
            if pid == os.getpid():
                continue
            log(f"stopping relay pid={pid}")
            _taskkill_tree(pid)
        self.proc = None
        self._relay_pids = set()
        if self._relay_fh:
            try:
                self._relay_fh.close()
            except Exception:
                pass
            self._relay_fh = None

    def wait_relay(self, timeout: float = 45.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.relay_up():
                return True
            if self.proc and self.proc.poll() is not None:
                # Launcher exited before readiness: whether that is a clean
                # failure or a half-started stub, report the current state.
                time.sleep(0.5)
                return self.relay_up()
            time.sleep(0.7)
        return self.relay_up()

    def recover_if_dead(self) -> None:
        """Health-gated self-heal for the monitor loop.

        Restarts the relay only when it is distinctly unhealthy: the process is
        gone, or nothing/plain-HTTP is wrong.  This is what turns the old
        "icon just turns red" behaviour into an automatic recovery.
        """
        if self.relay_up():
            return
        log("health check failed; attempting relay recovery")
        if not self.start_relay():
            return
        ready = self.wait_relay()
        log(f"relay recovery ready={ready} port={self.port}")

    # ---- auth helper --------------------------------------------
    def auth_running(self) -> bool:
        return bool(self.auth_proc and self.auth_proc.poll() is None)

    def start_auth(self) -> None:
        if self.auth_running():
            return
        self._auth_fh = self._open_log(AUTH_LOG, "auth helper start")
        self.auth_proc = subprocess.Popen(
            [
                self._python(), str(ROOT / "web_login.py"),
                "--relay", f"http://127.0.0.1:{self.port}",
                "--port", str(AUTH_LISTENER_PORT),
            ],
            cwd=str(ROOT),
            stdout=self._auth_fh,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_FLAGS,
        )
        log(f"auth helper started pid={self.auth_proc.pid}")

    def stop_auth(self) -> None:
        self._kill(self.auth_proc)
        self.auth_proc = None
        if self._auth_fh:
            try:
                self._auth_fh.close()
            except Exception:
                pass
            self._auth_fh = None

    def shutdown(self) -> None:
        self.stop_auth()
        self.stop_relay()


def main() -> int:
    lock = acquire_single_instance()
    if lock is None:
        message_box("Trae2api-cn 托盘已在运行。\n（右下角系统托盘查看）", "Trae2api-cn", 0x40)
        try:
            env = read_env()
            webbrowser.open(f"http://127.0.0.1:{env.get('PORT', '8000')}/web/login")
        except Exception:
            pass
        return 0

    try:
        import pystray
    except Exception as exc:
        message_box(
            f"缺少依赖 pystray/Pillow：{exc}\n\n请先运行：\n"
            f".venv\\Scripts\\pip install -r requirements-tray.txt",
            "Trae2api-cn",
            0x30,
        )
        return 1

    env = read_env()
    host = env.get("HOST", "0.0.0.0") or "0.0.0.0"
    port = int(env.get("PORT", "8000") or "8000")
    console_url = f"http://127.0.0.1:{port}/web/login"

    mgr = Manager(host, port)
    started = mgr.start_relay()
    ready = started and mgr.wait_relay()
    log(f"relay ready={ready} port={port}")

    icon_holder: dict[str, object] = {}

    def open_console(icon=None, item=None):
        webbrowser.open(console_url)

    def toggle_auth(icon=None, item=None):
        if mgr.auth_running():
            mgr.stop_auth()
        else:
            mgr.start_auth()
        icon.update_menu()

    def restart(icon=None, item=None):
        def worker():
            mgr.stop_relay()
            if mgr.start_relay():
                mgr.wait_relay()
            refresh()
        threading.Thread(target=worker, daemon=True).start()

    def open_log(icon=None, item=None):
        try:
            if RELAY_LOG.exists():
                os.startfile(str(RELAY_LOG))
            elif LOG_DIR.exists():
                os.startfile(str(LOG_DIR))
        except Exception as exc:
            log(f"open log error: {exc}")

    def quit_app(icon=None, item=None):
        log("quit requested")
        mgr.shutdown()
        icon.stop()

    def auth_label(item):
        return "停止授权助手" if mgr.auth_running() else "启动授权助手"

    menu = pystray.Menu(
        pystray.MenuItem("打开控制台", open_console, default=True),
        pystray.MenuItem(auth_label, toggle_auth),
        pystray.MenuItem("重启服务", restart),
        pystray.MenuItem("查看日志", open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )

    icon = pystray.Icon(
        "trae2api-cn",
        make_state_image("ok" if ready else "off"),
        "Trae2api-cn 运行中" if ready else "Trae2api-cn 启动中/异常",
        menu,
    )
    icon_holder["icon"] = icon

    def refresh():
        up = mgr.relay_up()
        try:
            icon.icon = make_state_image("ok" if up else "off")
            icon.title = (
                f"Trae2api-cn 运行中 · 端口 {port}" if up
                else "Trae2api-cn 已停止/异常，右键可重启"
            )
        except Exception:
            pass

    recovering = threading.Event()

    def monitor():
        # Consecutive failed health checks before a self-heal attempt.  A
        # single blip (e.g. a request in flight) must not trigger a restart.
        misses = 0
        while True:
            time.sleep(5)
            try:
                if mgr.relay_up():
                    misses = 0
                    refresh()
                    continue
                misses += 1
                refresh()
                if misses >= 3 and not recovering.is_set():
                    recovering.set()
                    try:
                        mgr.recover_if_dead()
                    finally:
                        misses = 0
                        recovering.clear()
            except Exception:
                pass

    threading.Thread(target=monitor, daemon=True).start()

    if not ready:
        message_box(
            f"服务未在 {port} 端口就绪，可能端口被占用或启动失败。\n"
            f"请在托盘右键『查看日志』查看 logs\\relay.log。",
            "Trae2api-cn",
            0x30,
        )

    try:
        icon.run()
    finally:
        mgr.shutdown()
        try:
            lock.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback

        log("FATAL:\n" + traceback.format_exc())
        raise

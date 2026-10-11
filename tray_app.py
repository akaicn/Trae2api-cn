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

# Health-probe tuning.  ``/v1/status`` runs on the same single uvicorn event
# loop as chat traffic, so a multi-megabyte request body being parsed can stall
# it well past a short timeout.  A missed probe is therefore *not* proof the
# relay is dead: we only force a restart when the process is actually gone, or
# after the relay has stayed unresponsive for a long stretch.
HEALTH_PROBE_TIMEOUT = float(os.environ.get("TRAY_HEALTH_TIMEOUT", "10"))
HEALTH_MISS_THRESHOLD = int(os.environ.get("TRAY_HEALTH_MISSES", "3"))
HEALTH_HARD_RESTART_SECONDS = float(os.environ.get("TRAY_HEALTH_HARD_SECONDS", "180"))

# The relay console page's <title>; used to find an already-open console, both
# as a window title (active tab) and as a browser tab item (UI Automation).
CONSOLE_TITLE = "Trae CN Relay 控制台"

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


def _find_window(substrings) -> int:
    """HWND of the first visible top-level window whose title contains any of
    ``substrings`` (case-insensitive); 0 when none matches.

    Used to focus an already-open console window instead of spawning another.
    """
    if os.name != "nt":
        return 0
    try:
        user32 = ctypes.windll.user32
        user32.EnumWindows.restype = ctypes.c_bool
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar), ctypes.c_int
        ]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_bool
    except Exception:
        return 0

    needles = [s.lower() for s in substrings]
    found: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.lower()
        if any(n in title for n in needles):
            found.append(hwnd)
            return False
        return True

    try:
        user32.EnumWindows(enum_proc(_callback), 0)
    except Exception:
        return 0
    return found[0] if found else 0


def _focus_window(hwnd: int) -> bool:
    """Bring an existing window to the foreground; True on success.

    Windows refuses ``SetForegroundWindow`` unless the caller owns the
    foreground.  We try, in order: a plain call; attaching our input queue to
    the current foreground thread; and finally briefly nudging the ALT key,
    which makes the OS treat our thread as the last input source and is the
    only technique that works under Edge on this machine.
    """
    if os.name != "nt" or not hwnd:
        return False
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.IsIconic.argtypes = [ctypes.c_void_p]
        user32.IsIconic.restype = ctypes.c_bool
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.SetForegroundWindow.restype = ctypes.c_bool
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool]
        user32.AttachThreadInput.restype = ctypes.c_bool
        user32.keybd_event.argtypes = [
            ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_void_p
        ]
        kernel32.GetCurrentThreadId.restype = ctypes.c_ulong

        # 9 = SW_RESTORE (un-minimize), 5 = SW_SHOW
        user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)
        if user32.SetForegroundWindow(hwnd):
            return True

        # Attempt 2: borrow the foreground thread's input queue and retry.
        current = kernel32.GetCurrentThreadId()
        foreground = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        if fg_thread and fg_thread != current and user32.AttachThreadInput(current, fg_thread, True):
            try:
                if user32.SetForegroundWindow(hwnd):
                    return True
            finally:
                user32.AttachThreadInput(current, fg_thread, False)

        # Attempt 3: the ALT-key nudge.
        user32.keybd_event(VK_MENU, 0, 0, 0)
        try:
            return bool(user32.SetForegroundWindow(hwnd))
        finally:
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        return False


def _activate_console_tab() -> bool:
    """Focus the console even when it sits in a background browser tab.

    A window title only reflects the *active* tab, so a title match cannot find
    a console hidden behind other tabs.  UI Automation can: it exposes every
    browser tab as a TabItem, so we ask it to select the one carrying the
    console title, then raise the owning window.  Returns True when a tab was
    found and focused.
    """
    if os.name != "nt":
        return False
    script = (
        "Add-Type -AssemblyName UIAutomationClient;"
        "Add-Type -AssemblyName UIAutomationTypes;"
        "$r=[System.Windows.Automation.AutomationElement]::RootElement;"
        "$c=New-Object System.Windows.Automation.PropertyCondition("
        "[System.Windows.Automation.AutomationElement]::ControlTypeProperty,"
        "[System.Windows.Automation.ControlType]::TabItem);"
        "$t=$r.FindAll([System.Windows.Automation.TreeScope]::Descendants,$c);"
        "$hit=$null;"
        "foreach($x in $t){if($x.Current.Name -like '*" + CONSOLE_TITLE + "*'){$hit=$x;break}};"
        "if($hit){$p=$null;"
        "if($hit.TryGetCurrentPattern("
        "[System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$p)){$p.Select()};"
        "$w=[System.Windows.Automation.TreeWalker]::ControlViewWalker;$e=$hit;"
        "while($e -ne $null -and $e.Current.ControlType -ne "
        "[System.Windows.Automation.ControlType]::Window){$e=$w.GetParent($e)};"
        "if($e){'HWND='+$e.Current.NativeWindowHandle}}"
    )
    try:
        out = _run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=25,
        ).stdout
    except Exception as exc:
        log(f"console tab lookup failed: {exc}")
        return False
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("HWND="):
            try:
                hwnd = int(line[len("HWND="):])
            except ValueError:
                continue
            return _focus_window(hwnd)
    return False


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


def _run_hidden(args, timeout: float):
    """Run a console helper with no visible console window.

    The tray runs under ``pythonw.exe`` (a GUI process with no console), so any
    console tool it spawns (``netstat`` / ``tasklist`` / ``taskkill`` /
    ``powershell``) would otherwise get a brand-new console window from Windows
    for every single call -- which is what produced the burst of black windows
    on quit.  ``CREATE_NO_WINDOW`` plus ``DEVNULL`` keeps every call silent.
    """
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


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
            _run_hidden(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=15)
        else:
            os.kill(pid, 9)
    except Exception as exc:
        log(f"taskkill pid={pid} error: {exc}")


def _listening_pids(port: int) -> set:
    """Return PIDs that currently LISTEN on ``port`` (TCP, IPv4 + IPv6)."""
    pids: set = set()
    try:
        out = _run_hidden(["netstat", "-ano", "-p", "tcp"], timeout=15).stdout
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
        out = _run_hidden(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
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
        out = _run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
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
        # When the health probe first started failing while the relay process
        # was still alive.  Lets the monitor tell a briefly-busy event loop
        # apart from a genuinely hung process.
        self._unhealthy_since: float | None = None
        # Set while the tray is quitting.  The monitor loop and any in-flight
        # restart worker must not resurrect the relay once this is set.
        self.stopping = threading.Event()

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
        return http_ok(
            f"http://127.0.0.1:{self.port}/v1/status",
            timeout=HEALTH_PROBE_TIMEOUT,
        )

    def relay_listening(self) -> bool:
        """Whether any process currently accepts TCP connections on our port.

        A listening socket plus a failed ``/v1/status`` means the event loop is
        busy, not that the relay died, so the monitor must not force-kill it.
        """
        return bool(_listening_pids(self.port))

    def start_relay(self) -> bool:
        if self.stopping.is_set():
            return False
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
                # Selector-based loop: uvicorn's Windows default (Proactor)
                # closes the listening socket on a transient accept error such
                # as WinError 64, which leaves the process alive but the port
                # dead.  Selector treats those errors as non-fatal.
                "--loop", "src.asyncio_loop:loop_factory",
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

    def recover_if_dead(self, force: bool = False) -> None:
        """Health-gated self-heal for the monitor loop.

        Restarts the relay only when it is distinctly unhealthy: the process is
        gone, or it has stayed unresponsive past ``HEALTH_HARD_RESTART_SECONDS``
        (the event loop may simply be parsing a huge request, which is not a
        failure).  ``force`` is used for user-initiated restarts.

        This is what turns the old "icon just turns red" behaviour into an
        automatic recovery -- and what stops the tray from killing a perfectly
        healthy relay just because one probe timed out under load.
        """
        if self.stopping.is_set():
            return
        if self.relay_up():
            self._unhealthy_since = None
            return
        if not force:
            if self.relay_listening():
                # The port is still bound: the event loop is merely busy (e.g.
                # parsing a huge request body), which is not a failure.  Only
                # escalate to a restart once it has stayed unresponsive for a
                # long time.
                now = time.time()
                if self._unhealthy_since is None:
                    self._unhealthy_since = now
                waited = now - self._unhealthy_since
                if waited < HEALTH_HARD_RESTART_SECONDS:
                    log(
                        f"health probe slow/failed but port still listening "
                        f"({waited:.0f}s); deferring forced restart"
                    )
                    return
                log(
                    f"relay unresponsive for {waited:.0f}s despite listening "
                    f"port; forcing restart"
                )
            # No listener on the port: the accept loop is dead (e.g. Windows
            # ProactorEventLoop closing the socket on WinError 64), so a live
            # process is not serving anyone.  Recover immediately.
        log("health check failed; attempting relay recovery")
        # Reap the old relay first.  A deaf-but-alive interpreter is no longer
        # listening, so the port scan in ``start_relay`` would miss it and leak
        # one process per recovery; ``stop_relay`` also matches it by command
        # line.
        self.stop_relay()
        if not self.start_relay():
            return
        ready = self.wait_relay()
        self._unhealthy_since = None if ready else time.time()
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
        # Set the stop flag first: it tells the monitor loop and any in-flight
        # restart worker not to revive the relay while we tear everything down.
        self.stopping.set()
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
        # Fast path: the console is the *active* tab, so its title is visible.
        existing = _find_window([CONSOLE_TITLE])
        if existing and _focus_window(existing):
            return
        # Slow path: the console may sit in a *background* tab, which the
        # window title cannot reveal.  Ask UI Automation to select it.
        if _activate_console_tab():
            return
        # Not open anywhere: open a fresh browser window.
        webbrowser.open(console_url)

    def toggle_auth(icon=None, item=None):
        if mgr.auth_running():
            mgr.stop_auth()
        else:
            mgr.start_auth()
        icon.update_menu()

    def restart(icon=None, item=None):
        def worker():
            if mgr.stopping.is_set():
                return
            # A user-initiated restart always wins, even while the relay still
            # answers probes: that is the whole point of the menu item.
            mgr.stop_relay()
            if mgr.stopping.is_set():
                return
            if mgr.start_relay():
                mgr.wait_relay()
            mgr._unhealthy_since = None
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
        if mgr.stopping.is_set():
            return
        log("quit requested")
        # Signal stop first so the monitor loop cannot "self-heal" the relay
        # while we are quitting, then drop the tray icon immediately instead of
        # leaving it (turning red) until the slow child cleanup finishes.  The
        # actual shutdown runs in the ``icon.run()`` finally block below.
        mgr.stopping.set()
        try:
            icon.stop()
        except Exception as exc:
            log(f"icon.stop error: {exc}")

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
        while not mgr.stopping.is_set():
            time.sleep(5)
            if mgr.stopping.is_set():
                return
            try:
                if mgr.relay_up():
                    misses = 0
                    mgr._unhealthy_since = None
                    refresh()
                    continue
                misses += 1
                refresh()
                if misses >= HEALTH_MISS_THRESHOLD and not recovering.is_set():
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

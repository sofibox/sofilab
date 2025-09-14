#!/usr/bin/env python3
"""
SofiLab - Cross-platform Python CLI

Feature parity with the existing bash tool (sofilab.sh) for:
- login, reset-hostkey, status, run-scripts, run-script, reboot, list-scripts,
  logs, clear-logs, install, uninstall, --version, --help

Notes:
- Uses Paramiko for SSH/SFTP to be cross-platform (Windows/macOS/Linux).
- Preserves config format from `sofilab.conf` and global logging options.
- Stores logs in `logs/` with rotation similar to bash version.
"""
from __future__ import annotations

import argparse
import dataclasses
import errno
import getpass
import io
import os
import re
import select
import shlex
import signal
import socket
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import importlib
import subprocess
import stat
import posixpath
import shutil

# Paramiko is loaded lazily. If missing, we will auto-install from requirements.txt.
PARAMIKO_MOD = None  # type: ignore

def ensure_paramiko():
    """Return a working paramiko module or raise with a clear message.

    Handles environments where a shadowing module exists or a partial install
    returns an invalid object. Attempts auto-install/repair once.
    """
    global PARAMIKO_MOD
    if PARAMIKO_MOD is not None:
        return PARAMIKO_MOD

    def _import_paramiko():
        mod = importlib.import_module("paramiko")
        if mod is None or not hasattr(mod, "SSHClient"):
            raise ImportError("paramiko imported but invalid (missing SSHClient)")
        return mod

    try:
        PARAMIKO_MOD = _import_paramiko()
        return PARAMIKO_MOD
    except Exception:
        # Try to install dependencies automatically
        req = SCRIPT_DIR / "requirements.txt"
        cmd = [sys.executable, "-m", "pip", "install"]
        if req.exists():
            cmd += ["-r", str(req)]
            print("Installing Python dependencies from requirements.txt...", file=sys.stderr)
        else:
            cmd += ["paramiko>=3.4.0"]
            print("Installing Python dependency: paramiko...", file=sys.stderr)
        try:
            try:
                # Remove any preloaded/invalid module
                if "paramiko" in sys.modules:
                    del sys.modules["paramiko"]
            except Exception:
                pass
            subprocess.check_call(cmd)
        except Exception as e:
            print(f"❌ Failed to install dependencies automatically: {e}", file=sys.stderr)
            print("Please run: python -m pip install paramiko>=3.4.0", file=sys.stderr)
            raise
        PARAMIKO_MOD = _import_paramiko()
        return PARAMIKO_MOD


# --------------------------
# Windows helpers
# --------------------------
def _win_local_appdata() -> Optional[Path]:
    """Return LocalAppData path using Win32 API for robustness on Windows.
    Falls back to environment variable or user home if needed.
    """
    if os.name != 'nt':
        return None
    # Try Win32 SHGetKnownFolderPath(FOLDERID_LocalAppData)
    try:
        import ctypes
        from ctypes import wintypes

        # FOLDERID_LocalAppData {F1B32785-6FBA-4FCF-9D55-7B8E7F157091}
        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        FOLDERID_LocalAppData = GUID(0xF1B32785, 0x6FBA, 0x4FCF, (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91))

        SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
        SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        SHGetKnownFolderPath.restype = ctypes.c_long

        path_ptr = ctypes.c_wchar_p()
        # Flags = 0 (default). Token = None (current user)
        hr = SHGetKnownFolderPath(ctypes.byref(FOLDERID_LocalAppData), 0, None, ctypes.byref(path_ptr))
        if hr == 0 and path_ptr.value:
            try:
                return Path(path_ptr.value)
            finally:
                try:
                    ctypes.windll.ole32.CoTaskMemFree(path_ptr)
                except Exception:
                    pass
    except Exception:
        pass

    # Fallbacks
    try:
        env = os.environ.get("LOCALAPPDATA")
        if env:
            return Path(env)
    except Exception:
        pass
    try:
        return Path.home() / "AppData" / "Local"
    except Exception:
        return None

# --------------------------
# Metadata
# --------------------------
VERSION = "1.0.0-Python"
BUILD_DATE = "2025-09-10"
AUTHOR = "Arafat Ali <arafat@sofibox.com>"


# --------------------------
# Paths and environment
# --------------------------
SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
SCRIPT_NAME = SCRIPT_PATH.name
CONFIG_FILE = SCRIPT_DIR / "sofilab.conf"


# --------------------------
# Logging (Rotating)
# --------------------------
import logging
from logging.handlers import RotatingFileHandler


@dataclasses.dataclass
class GlobalConfig:
    log_dir: Path = SCRIPT_DIR / "logs"
    log_level: str = "INFO"  # DEBUG, INFO, WARN, ERROR
    enable_logging: bool = True
    max_log_size: str = "10M"  # K/M/G
    max_log_files: int = 5
    script_exit_on_error: bool = True
    force_tty: bool = True

    def max_bytes(self) -> int:
        s = self.max_log_size.strip().upper()
        if s.endswith("G"):
            return int(s[:-1]) * 1024 * 1024 * 1024
        if s.endswith("M"):
            return int(s[:-1]) * 1024 * 1024
        if s.endswith("K"):
            return int(s[:-1]) * 1024
        return int(s)


MAIN_LOG: Optional[Path] = None
ERROR_LOG: Optional[Path] = None
REMOTE_LOG: Optional[Path] = None


def init_logging(cfg: GlobalConfig) -> None:
    global MAIN_LOG, ERROR_LOG, REMOTE_LOG
    if not cfg.enable_logging:
        return

    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    MAIN_LOG = cfg.log_dir / "sofilab.log"
    ERROR_LOG = cfg.log_dir / "sofilab-error.log"
    REMOTE_LOG = cfg.log_dir / "sofilab-remote.log"

    # Root logger setup
    root = logging.getLogger("sofilab")
    root.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
    root.handlers[:] = []

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")

    if MAIN_LOG:
        h_main = RotatingFileHandler(MAIN_LOG, maxBytes=cfg.max_bytes(), backupCount=cfg.max_log_files, encoding="utf-8")
        h_main.setFormatter(fmt)
        h_main.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
        root.addHandler(h_main)

    # Separate error file
    if ERROR_LOG:
        h_err = RotatingFileHandler(ERROR_LOG, maxBytes=cfg.max_bytes(), backupCount=cfg.max_log_files, encoding="utf-8")
        h_err.setFormatter(fmt)
        h_err.setLevel(logging.ERROR)
        root.addHandler(h_err)


def log_remote(alias: str, script: str, line: str) -> None:
    global REMOTE_LOG
    if REMOTE_LOG is None:
        return
    try:
        REMOTE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REMOTE_LOG.open("a", encoding="utf-8", errors="ignore") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] [{alias}] [{script}] {line}\n")
    except Exception:
        pass


log = logging.getLogger("sofilab")


def info(msg: str) -> None:
    print(f"💡 {msg}", file=sys.stderr)
    log.info(msg)


def warn(msg: str) -> None:
    print(f"⚠️  {msg}", file=sys.stderr)
    log.warning(msg)


def error(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    log.error(msg)


def success(msg: str) -> None:
    print(f"✅ {msg}", file=sys.stderr)
    log.info("SUCCESS: %s", msg)


def progress(msg: str) -> None:
    print(f"🔄 {msg}", file=sys.stderr)
    log.info("PROGRESS: %s", msg)


# --------------------------
# Utilities
# --------------------------
def resolve_host_ip(host: str) -> Optional[str]:
    try:
        # Prefer IPv4 if possible
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                infos = socket.getaddrinfo(host, None, family, socket.SOCK_STREAM)
                if infos:
                    return infos[0][4][0]
            except socket.gaierror:
                continue
    except Exception:
        pass
    return None


def check_port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def human_size(nbytes: int) -> str:
    suffixes = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    f = float(nbytes)
    while f >= 1024 and i < len(suffixes) - 1:
        f /= 1024.0
        i += 1
    if i == 0:
        return f"{int(f)}{suffixes[i]}"
    return f"{f:.1f}{suffixes[i]}"


# --------------------------
# Config parsing
# --------------------------
@dataclasses.dataclass
class ServerConfig:
    aliases: List[str]
    host: str
    user: str
    password: str = ""
    port: int = 22
    keyfile: str = ""
    scripts: List[str] = dataclasses.field(default_factory=list)
    # New: per-script and default arguments
    script_args_map: Dict[str, List[str]] = dataclasses.field(default_factory=dict)
    default_script_args: List[str] = dataclasses.field(default_factory=list)


def parse_conf(path: Path) -> Tuple[GlobalConfig, Dict[str, ServerConfig]]:
    if not path.exists():
        raise FileNotFoundError(errno.ENOENT, "Configuration file not found", str(path))

    gcfg = GlobalConfig()
    servers: Dict[str, ServerConfig] = {}

    section: Optional[str] = None
    section_aliases: List[str] = []
    acc: Dict[str, str] = {}

    def flush_server() -> None:
        nonlocal servers, section_aliases, acc
        if not section_aliases:
            return
        host = acc.get("host", "").strip()
        user = acc.get("user", "").strip()
        if not host or not user:
            # Skip invalid blocks
            return
        password = acc.get("password", "").strip()
        port_s = acc.get("port", "22").strip()
        try:
            port = int(port_s)
        except ValueError:
            port = 22
        keyfile = acc.get("keyfile", "").strip()
        scripts_s = acc.get("scripts", "").strip()
        scripts = [s.strip() for s in scripts_s.split(',') if s.strip()] if scripts_s else []

        # Support inline args inside scripts entries, e.g.:
        # scripts="foo.sh --x 1, bar.sh 'arg with space'"
        inline_args_map: Dict[str, List[str]] = {}
        normalized_scripts: List[str] = []
        for item in scripts:
            try:
                parts = shlex.split(item)
            except Exception:
                parts = [p for p in item.split() if p]
            if not parts:
                continue
            name = parts[0]
            if len(parts) > 1:
                inline_args_map[name] = parts[1:]
            normalized_scripts.append(name)
        scripts = normalized_scripts

        # Parse script args from keys like script_args.<script>="arg1 arg2" and default_script_args
        script_args_map: Dict[str, List[str]] = {}
        default_script_args: List[str] = []
        for k, v in acc.items():
            if k.startswith("script_args.") or k.startswith("script-args."):
                name = k.split('.', 1)[1].strip()
                try:
                    script_args_map[name] = shlex.split(v)
                except Exception:
                    # Fall back to space split
                    script_args_map[name] = [p for p in v.split() if p]
            elif k in ("default_script_args", "default-script-args"):
                try:
                    default_script_args = shlex.split(v)
                except Exception:
                    default_script_args = [p for p in v.split() if p]

        # Merge inline args; explicit keys override inline
        for _n, _a in inline_args_map.items():
            if _n not in script_args_map:
                script_args_map[_n] = _a

        sc = ServerConfig(section_aliases, host, user, password, port, keyfile, scripts, script_args_map, default_script_args)
        for a in section_aliases:
            servers[a] = sc

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue

            m = re.match(r"^\[(.+)\]$", line)
            if m:
                # flush previous
                if section == "global":
                    pass  # nothing to flush
                else:
                    flush_server()
                section = m.group(1).strip()
                if section.lower() == "global":
                    section = "global"
                    section_aliases = []
                    acc = {}
                else:
                    section_aliases = [a.strip() for a in section.split(',') if a.strip()]
                    acc = {}
                continue

            # key=value
            kv = re.match(r"^([^=]+)=(.*)$", line)
            if not kv or section is None:
                continue

            key = kv.group(1).strip()
            val = kv.group(2).strip()
            # remove optional quotes
            if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
                val = val[1:-1]

            if section == "global":
                if key == "log_dir":
                    p = Path(val)
                    gcfg.log_dir = p if p.is_absolute() else (SCRIPT_DIR / p)
                elif key == "log_level":
                    if val.upper() in {"DEBUG", "INFO", "WARN", "ERROR"}:
                        gcfg.log_level = val.upper()
                elif key == "enable_logging":
                    gcfg.enable_logging = val.lower() == "true"
                elif key == "max_log_size":
                    gcfg.max_log_size = val
                elif key == "max_log_files":
                    try:
                        gcfg.max_log_files = max(1, int(val))
                    except ValueError:
                        pass
                elif key == "script_exit_on_error":
                    gcfg.script_exit_on_error = val.lower() == "true"
                elif key == "force_tty":
                    gcfg.force_tty = val.lower() == "true"
                else:
                    warn(f"Unknown global configuration key: {key}")
            else:
                acc[key] = val

    # flush last
    if section != "global":
        flush_server()

    return gcfg, servers


# --------------------------
# SSH/Paramiko helpers
# --------------------------
class SSHClient:
    def __init__(self, host: str, port: int, username: str, password: str = "", key_path: Optional[Path] = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_path = key_path
        self.client: Optional[object] = None
        self.transport: Optional[object] = None

    def connect(self, timeout: float = 5.0) -> None:
        paramiko = ensure_paramiko()
        c = paramiko.SSHClient()
        # Automatically accept and add new host keys (aligns with bash StrictHostKeyChecking=accept-new)
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        key_filename = None
        if self.key_path and self.key_path.exists():
            key_filename = str(self.key_path)

        # Try agent/keys first without prompting for passphrases
        try:
            c.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=None,
                key_filename=key_filename,
                look_for_keys=True,
                allow_agent=True,
                timeout=timeout,
                auth_timeout=timeout,
                banner_timeout=timeout,
            )
        except paramiko.AuthenticationException:
            # Fallback to password auth if provided (do not prompt for key passphrase)
            if self.password:
                c.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    look_for_keys=False,
                    allow_agent=True,
                    timeout=timeout,
                    auth_timeout=timeout,
                    banner_timeout=timeout,
                )
            else:
                raise
        self.client = c
        self.transport = c.get_transport()

    def close(self) -> None:
        try:
            if self.client:
                self.client.close()
        finally:
            self.client = None
            self.transport = None

    def run(self, command: str, env: Optional[Dict[str, str]] = None, get_pty: bool = False, timeout: Optional[float] = None) -> Tuple[int, str, str]:
        assert self.client is not None, "SSH not connected"
        chan = self.client.get_transport().open_session()
        if get_pty:
            chan.get_pty()
        if env:
            # Paramiko supports env on exec_command via Channel
            for k, v in env.items():
                chan.update_environment({k: v})
        chan.exec_command(command)
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        start = time.time()
        while True:
            if chan.recv_ready():
                stdout.write(chan.recv(32768))
            if chan.recv_stderr_ready():
                stderr.write(chan.recv_stderr(32768))
            if chan.exit_status_ready():
                # flush remaining
                while chan.recv_ready():
                    stdout.write(chan.recv(32768))
                while chan.recv_stderr_ready():
                    stderr.write(chan.recv_stderr(32768))
                break
            if timeout is not None and (time.time() - start) > timeout:
                chan.close()
                break
            time.sleep(0.01)
        code = chan.recv_exit_status()
        return code, stdout.getvalue().decode(errors="ignore"), stderr.getvalue().decode(errors="ignore")

    def sftp(self):
        assert self.client is not None, "SSH not connected"
        return self.client.open_sftp()

    def interactive_shell(self) -> None:
        """Interactive shell bridging local TTY <-> remote PTY.
        On POSIX, switch local TTY to raw mode so line editing keys work
        (arrows, backspace, Ctrl-C, etc.), then restore on exit.
        """
        assert self.client is not None
        # Prefer 256-color term if available; fall back to xterm
        term_name = os.environ.get("TERM") or "xterm-256color"
        chan = self.client.invoke_shell(term=term_name)

        # Try to set window size if we can (POSIX only)
        try:
            import fcntl, struct, termios

            def get_winsize():
                s = struct.pack('HHHH', 0, 0, 0, 0)
                r = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, s)
                rows, cols, _, _ = struct.unpack('HHHH', r)
                return rows, cols

            rows, cols = get_winsize()
            chan.resize_pty(width=cols, height=rows)
        except Exception:
            pass

        if os.name == 'nt':
            # Windows: use msvcrt for keyboard input, and a loop for channel
            import threading, msvcrt

            stop = False

            def reader():
                while not stop:
                    data = chan.recv(32768)
                    if not data:
                        break
                    sys.stdout.write(data.decode(errors="ignore"))
                    sys.stdout.flush()

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            try:
                while True:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch == '\r':
                            chan.send("\r")
                        else:
                            chan.send(ch.encode('utf-8'))
                    if chan.exit_status_ready():
                        break
                    time.sleep(0.01)
            except KeyboardInterrupt:
                pass
            finally:
                stop = True
                try:
                    chan.close()
                except Exception:
                    pass
        else:
            # POSIX: put local TTY into raw mode and bridge bytes
            import termios, tty, signal

            fd = sys.stdin.fileno()
            old_attrs = None

            # Handle window resize: propagate to remote PTY
            def _on_winch(_sig, _frm):
                try:
                    import fcntl, struct, termios as _t
                    s = struct.pack('HHHH', 0, 0, 0, 0)
                    r = fcntl.ioctl(fd, _t.TIOCGWINSZ, s)
                    rows, cols, _, _ = struct.unpack('HHHH', r)
                    try:
                        chan.resize_pty(width=cols, height=rows)
                    except Exception:
                        pass
                except Exception:
                    pass

            try:
                if sys.stdin.isatty():
                    old_attrs = termios.tcgetattr(fd)
                    tty.setraw(fd)
                    signal.signal(signal.SIGWINCH, _on_winch)
                while True:
                    rlist, _, _ = select.select([chan, sys.stdin], [], [])
                    if chan in rlist:
                        data = chan.recv(32768)
                        if not data:
                            break
                        sys.stdout.write(data.decode(errors="ignore"))
                        sys.stdout.flush()
                    if sys.stdin in rlist:
                        try:
                            data = os.read(fd, 1024)
                        except Exception:
                            data = b""
                        if not data:
                            break
                        try:
                            chan.send(data)
                        except Exception:
                            break
            except KeyboardInterrupt:
                pass
            finally:
                if old_attrs is not None:
                    try:
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
                    except Exception:
                        pass
                try:
                    chan.close()
                except Exception:
                    pass


# --------------------------
# Core features
# --------------------------
def determine_ssh_port(configured_port: int, host: str) -> Optional[int]:
    display_host = host
    rip = resolve_host_ip(host)
    if rip and rip != host:
        display_host = f"{host} ({rip})"

    progress(f"Checking connection to {display_host}:{configured_port}...")
    if check_port_open(host, configured_port):
        if configured_port == 22:
            info("Port 22 is open (default SSH port)")
        else:
            info(f"Port {configured_port} is open (custom SSH port)")
        return configured_port

    if configured_port != 22:
        progress(f"Port {configured_port} not accessible, trying fallback port 22...")
        if check_port_open(host, 22):
            info("Port 22 is open (fallback to default SSH port)")
            return 22
        error(f"Neither port {configured_port} nor port 22 are accessible")
        return None
    else:
        error(f"Port {configured_port} is not accessible")
        return None


def get_ssh_keyfile(sc: ServerConfig) -> Optional[Path]:
    # Explicit keyfile
    if sc.keyfile:
        p = Path(sc.keyfile)
        if not p.is_absolute():
            p = SCRIPT_DIR / p
        if p.exists():
            info(f"Using SSH key: {p}")
            return p

    # Auto-detect ssh/<alias>_key
    for alias in sc.aliases:
        p = SCRIPT_DIR / "ssh" / f"{alias}_key"
        if p.exists():
            info(f"Using SSH key: {p}")
            return p
    return None


def server_status(sc: ServerConfig, port_override: Optional[int] = None) -> int:
    port_to_check = port_override if port_override else determine_ssh_port(sc.port, sc.host)
    if not port_to_check:
        return 1

    print("")
    print("🩺 Server Status")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📍 Host: {sc.host}")
    print(f"🔌 SSH Port: {port_to_check}")

    if check_port_open(sc.host, port_to_check):
        success("Port reachable")
    else:
        error("Port not reachable")
        return 1

    keyfile = get_ssh_keyfile(sc)
    auth_ok = False
    # Try key
    try:
        cli = SSHClient(sc.host, port_to_check, sc.user, key_path=keyfile)
        cli.connect(timeout=5)
        auth_ok = True
        print("🔐 Auth: SSH key works")
        # also try a simple command
        code, out, _ = cli.run("uname -a && uptime", timeout=5)
        if code == 0 and out:
            success("Retrieved basic system info")
            for ln in out.splitlines():
                print("   " + ln)
    except Exception:
        pass
    finally:
        try:
            cli.close()  # type: ignore
        except Exception:
            pass

    if not auth_ok and sc.password:
        try:
            cli = SSHClient(sc.host, port_to_check, sc.user, password=sc.password)
            cli.connect(timeout=5)
            auth_ok = True
            print("🔐 Auth: Password works")
        except Exception:
            pass
        finally:
            try:
                cli.close()  # type: ignore
            except Exception:
                pass

    if not auth_ok:
        print("🔐 Auth: Unknown (may require interactive password or key not found)")

    return 0


## test_speed removed (feature deferred)

def router_webui(sc: ServerConfig, action: str) -> int:
    """Enable/disable ASUS Merlin web UI (WAN) on router hosts.

    Only runs when the server's alias list includes 'router'.
    """
    aliases_lower = {a.lower() for a in sc.aliases}
    if "router" not in aliases_lower:
        error("This command is restricted to hosts with alias 'router'")
        return 1

    use_port = determine_ssh_port(sc.port, sc.host)
    if not use_port:
        return 1

    keyfile = get_ssh_keyfile(sc)
    try:
        cli = SSHClient(sc.host, use_port, sc.user, sc.password, keyfile)
        cli.connect(timeout=5)
    except Exception as e:
        error(f"SSH connection failed: {e}")
        return 1

    try:
        print("")
        print("🛠️  ASUS Merlin Web UI Control")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"📍 Host: {sc.host}:{use_port}")
        print(f"🎯 Action: {action}")
        print("")

        if action == "disable":
            cmd = (
                "sh -lc 'set -e; "
                "nvram set misc_http_x=0; "
                "nvram commit; "
                "service restart_firewall; "
                "service restart_httpd'"
            )
        else:  # enable
            cmd = (
                "sh -lc 'set -e; "
                "nvram set misc_http_x=2; "
                "nvram commit; "
                "service restart_firewall; "
                "service restart_httpd'"
            )

        progress("Applying NVRAM changes on router...")
        code, out, err = cli.run(cmd, get_pty=False, timeout=60)
        if out.strip():
            print(out.strip())
        if code != 0:
            error(f"Router command failed (exit {code}): {err.strip() or out.strip()}")
            return code
        success("Web UI setting updated and httpd restarted")
        return 0
    finally:
        try:
            cli.close()
        except Exception:
            pass

def _run_local_hook(command_name: str, sc: ServerConfig, alias: str, actual_port: int) -> Optional[int]:
    """Try to execute a local hook script for a command.

    Resolution order:
      1) scripts/<command>.py (cross-platform via current Python)
      2) Windows: scripts/<command>.ps1 via PowerShell
      3) POSIX: scripts/<command>.sh via bash/sh

    Returns the exit code if a hook was executed, or None if no hook found.
    """
    scripts_dir = SCRIPT_DIR / "scripts"
    hook_py = scripts_dir / f"{command_name}.py"
    hook_ps1 = scripts_dir / f"{command_name}.ps1"
    hook_sh = scripts_dir / f"{command_name}.sh"

    # Build environment for hooks
    env = os.environ.copy()
    env.update({
        "SOFILAB_HOST": sc.host,
        "SOFILAB_PORT": str(actual_port),
        "SOFILAB_USER": sc.user,
        "SOFILAB_PASSWORD": sc.password or "",
        "SOFILAB_KEYFILE": str(get_ssh_keyfile(sc) or ""),
        "SOFILAB_ALIAS": alias,
    })

    try:
        if hook_py.exists():
            cmd = [sys.executable, str(hook_py)]
            return subprocess.call(cmd, env=env)
        if os.name == 'nt' and hook_ps1.exists():
            # Prefer pwsh if available, else Windows PowerShell
            pwsh = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            cmd = [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(hook_ps1)]
            return subprocess.call(cmd, env=env)
        if os.name != 'nt' and hook_sh.exists():
            bash = shutil.which("bash")
            if bash:
                cmd = [bash, str(hook_sh)]
            else:
                cmd = ["/bin/sh", str(hook_sh)]
            return subprocess.call(cmd, env=env)
    except FileNotFoundError as e:
        warn(f"Hook interpreter not found: {e}")
        return 127
    except Exception as e:
        error(f"Hook execution error: {e}")
        return 1

    return None


def ssh_login(sc: ServerConfig, alias: str) -> int:
    port = determine_ssh_port(sc.port, sc.host)
    if not port:
        return 1
    resolved_ip = resolve_host_ip(sc.host) or ""

    # Header
    border = "=" * 70
    print(border)
    print(f"SofiLab • Server Management Tool by {AUTHOR}")
    print(f"Script: {SCRIPT_NAME}  Version: {VERSION} (Build {BUILD_DATE})")
    print("Action: Login")
    target = sc.host
    if resolved_ip and resolved_ip != sc.host:
        target += f" ({resolved_ip})"
    target += f" :{port}"
    print(f"Target: {target}")
    print(f"Hostname: {socket.gethostname()}  When: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(border)

    keyfile = get_ssh_keyfile(sc)

    # Optional local hook override (scripts/login.*)
    hook_rc = _run_local_hook("login", sc, alias, port)
    if hook_rc is not None:
        if hook_rc == 0:
            success("Login hook completed successfully")
        else:
            error(f"Login hook failed with exit code {hook_rc}")
        return hook_rc

    info(f"Attempting SSH connection on port {port}...")
    try:
        cli = SSHClient(sc.host, port, sc.user, sc.password, keyfile)
        cli.connect(timeout=5)
        info("Authentication successful")
        cli.interactive_shell()
        info(f"Disconnected from {sc.host}")
        return 0
    except Exception as e:
        error(f"Authentication/connection error: {e}")
        return 1
    finally:
        try:
            cli.close()  # type: ignore
        except Exception:
            pass


def reboot_server(sc: ServerConfig, wait_seconds: Optional[int]) -> int:
    port = determine_ssh_port(sc.port, sc.host)
    if not port:
        return 1

    keyfile = get_ssh_keyfile(sc)
    info(f"Issuing reboot on {sc.host}:{port} as {sc.user}")
    cmd = "systemctl reboot || reboot || shutdown -r now"

    try:
        cli = SSHClient(sc.host, port, sc.user, sc.password, keyfile)
        cli.connect(timeout=5)
        # Exec without waiting for long – server will close connection
        code, _out, _err = cli.run(cmd, timeout=5)
        info(f"Reboot command issued; SSH exit code: {code} (disconnect expected)")
    except Exception as e:
        # Connection drop is common; treat as issued
        warn(f"SSH error during reboot (likely due to disconnect): {e}")
    finally:
        try:
            cli.close()  # type: ignore
        except Exception:
            pass

    if not wait_seconds:
        success(f"Reboot initiated on {sc.host}")
        return 0

    # Wait for host to go down then come back
    down_timeout = 60
    waited = 0
    progress(f"Waiting for {sc.host} to go down...")
    while check_port_open(sc.host, port):
        time.sleep(2)
        waited += 2
        if waited >= down_timeout:
            warn(f"{sc.host}:{port} still reachable after {down_timeout}s; continuing")
            break

    progress(f"Waiting for {sc.host} to come back (up to {wait_seconds}s)...")
    waited = 0
    while not check_port_open(sc.host, port):
        time.sleep(3)
        waited += 3
        if waited >= wait_seconds:
            error(f"Timeout waiting for {sc.host}:{port} to come back")
            return 1
    success(f"Server is back online: {sc.host}:{port}")
    return 0


def upload_script(cli: SSHClient, local_script: Path, remote_rel_dir: str = ".sofilab_scripts") -> str:
    """Upload a local script to the remote host and return its POSIX path.

    Primary method: SFTP. Fallback: shell stream via 'cat > file' for servers
    without SFTP (e.g., Dropbear/BusyBox on some routers).
    Always return a forward-slash (POSIX) path.
    """
    remote_dir = f"{remote_rel_dir}"
    remote_path = f"{remote_dir}/{local_script.name}"

    # Try SFTP first
    try:
        sftp = cli.sftp()
        try:
            sftp.stat(remote_dir)
        except IOError:
            sftp.mkdir(remote_dir)
        sftp.put(str(local_script), remote_path)
        return remote_path
    except Exception as e:
        warn(f"SFTP unavailable on server, falling back to shell upload: {e}")

    # Fallback: upload via shell using cat redirect into file in HOME
    assert cli.client is not None, "SSH not connected"
    chan = cli.client.get_transport().open_session()
    # Ensure we are in HOME; make dir; then read stdin to file
    cmd = (
        f"sh -lc 'cd ~ && umask 077; mkdir -p {shlex.quote(remote_dir)} && "
        f"cat > {shlex.quote(remote_path)}'"
    )
    chan.exec_command(cmd)
    try:
        with local_script.open('rb') as f:
            while True:
                chunk = f.read(32768)
                if not chunk:
                    break
                try:
                    chan.sendall(chunk)
                except Exception:
                    break
        try:
            chan.shutdown_write()
        except Exception:
            pass
        # Drain any stderr for diagnostics
        _ = chan.recv_exit_status()
        return remote_path
    finally:
        try:
            chan.close()
        except Exception:
            pass


# --------------------------
# File transfer helpers (SFTP)
# --------------------------
def _sftp_home(sftp) -> str:
    """Return remote home directory as POSIX path."""
    try:
        return sftp.normalize(".")
    except Exception:
        return "/"


def _sftp_abs(sftp, remote_path: str) -> str:
    """Normalize a remote path to an absolute POSIX path.
    - `~` resolves to the SFTP session home
    - Relative paths are resolved from home
    """
    rp = remote_path or "."
    home = _sftp_home(sftp)
    if rp.startswith("~"):
        rp = rp.replace("~", home, 1)
    if not rp.startswith("/"):
        rp = posixpath.join(home, rp)
    # Collapse any ../ or ./
    parts = []
    for p in rp.split('/'):
        if not p or p == '.':
            continue
        if p == '..':
            if parts:
                parts.pop()
            continue
        parts.append(p)
    return "/" + "/".join(parts)


def _is_dir(attrs) -> bool:
    try:
        return stat.S_ISDIR(attrs.st_mode)
    except Exception:
        return False


def _list_remote_shell(cli: SSHClient, remote_dir: str) -> int:
    # Fallback listing via remote shell when SFTP subsystem is unavailable (e.g., Dropbear).
    # Prefer predictable output; BusyBox supports -al in most builds.
    token = (remote_dir or "~").strip()
    if token.startswith("~"):
        # Allow shell to expand ~ by not quoting it
        arg = token
    else:
        arg = shlex.quote(token)
    code, out, err = cli.run(f"sh -lc 'ls -al -- {arg}'", timeout=15)
    if code != 0:
        error(f"Remote ls failed (exit {code}): {err.strip() or out.strip()}")
        return 1
    print("")
    print(f"📂 Listing (shell): {token}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    sys.stdout.write(out)
    return 0


def sftp_list_directory(cli: SSHClient, remote_dir: str) -> int:
    try:
        sftp = cli.sftp()
    except Exception as e:
        warn(f"SFTP not available on server ({e}); falling back to shell ls")
        return _list_remote_shell(cli, remote_dir)

    target = _sftp_abs(sftp, remote_dir or ".")
    try:
        attrs = sftp.stat(target)
    except IOError as e:
        error(f"Remote path not found: {target} ({e})")
        return 1
    if not _is_dir(attrs):
        # If it's a file, just show that file
        info("Target is a file; displaying file info")
        name = posixpath.basename(target)
        print(f"- {name}")
        return 0

    print("")
    print(f"📂 Listing: {target}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    try:
        entries = sftp.listdir_attr(target)
    except Exception as e:
        warn(f"SFTP list failed ({e}); falling back to shell ls")
        return _list_remote_shell(cli, remote_dir)

    if not entries:
        print("(empty)")
        return 0

    for ent in sorted(entries, key=lambda a: a.filename.lower()):
        size = ent.st_size
        kind = 'd' if _is_dir(ent) else '-'
        # Basic columns: type, size, name
        print(f"{kind} {human_size(size):>7}  {ent.filename}")
    return 0


def _ensure_remote_dir(sftp, remote_dir: str) -> None:
    """Recursively create directories on remote if missing."""
    remote_dir = remote_dir.rstrip('/')
    if not remote_dir or remote_dir == '/':
        return
    parts = remote_dir.split('/')
    path = ''
    for p in parts:
        if not p:
            path = '/'
            continue
        path = p if path == '/' else (path + '/' + p) if path else p
        try:
            sftp.stat('/' + path if not path.startswith('/') else path)
        except IOError:
            try:
                sftp.mkdir('/' + path if not path.startswith('/') else path)
            except Exception:
                pass


def download_items(cli: SSHClient, remote_paths: List[str], local_dest: Path, recursive: bool) -> int:
    sftp = cli.sftp()
    home = _sftp_home(sftp)
    local_dest.mkdir(parents=True, exist_ok=True)

    def _download_file(remote_file_abs: str, local_dir: Path) -> None:
        local_path = local_dir / posixpath.basename(remote_file_abs)
        sftp.get(remote_file_abs, str(local_path))
        info(f"Downloaded: {remote_file_abs} -> {local_path}")

    def _walk_dir(remote_dir_abs: str, local_dir: Path):
        try:
            entries = sftp.listdir_attr(remote_dir_abs)
        except Exception as e:
            warn(f"Skip directory (cannot list): {remote_dir_abs} ({e})")
            return
        local_dir.mkdir(parents=True, exist_ok=True)
        for ent in entries:
            r_path = posixpath.join(remote_dir_abs, ent.filename)
            if _is_dir(ent):
                if recursive:
                    _walk_dir(r_path, local_dir / ent.filename)
                else:
                    info(f"Skip directory (use -r to recurse): {r_path}")
            else:
                _download_file(r_path, local_dir)

    any_error = 0
    for rp in remote_paths:
        rp_abs = _sftp_abs(sftp, rp)
        try:
            attrs = sftp.stat(rp_abs)
        except IOError as e:
            error(f"Remote path not found: {rp} ({e})")
            any_error = 1
            continue
        if _is_dir(attrs):
            _walk_dir(rp_abs, local_dest / posixpath.basename(rp_abs.rstrip('/')))
        else:
            _download_file(rp_abs, local_dest)

    return any_error


def upload_items(cli: SSHClient, local_paths: List[Path], remote_dest: str, recursive: bool) -> int:
    sftp = cli.sftp()
    dest_abs = _sftp_abs(sftp, remote_dest or ".")
    _ensure_remote_dir(sftp, dest_abs)

    def _upload_file(local_file: Path, dest_dir_abs: str) -> None:
        remote_path = posixpath.join(dest_dir_abs, local_file.name)
        sftp.put(str(local_file), remote_path)
        info(f"Uploaded: {local_file} -> {remote_path}")

    def _walk_local_dir(local_dir: Path, dest_dir_abs: str):
        # Ensure remote dir exists
        _ensure_remote_dir(sftp, dest_dir_abs)
        for entry in local_dir.iterdir():
            if entry.is_dir():
                if recursive:
                    _walk_local_dir(entry, posixpath.join(dest_dir_abs, entry.name))
                else:
                    info(f"Skip directory (use -r to recurse): {entry}")
            elif entry.is_file():
                _upload_file(entry, dest_dir_abs)

    any_error = 0
    for lp in local_paths:
        if not lp.exists():
            error(f"Local path not found: {lp}")
            any_error = 1
            continue
        if lp.is_dir():
            _walk_local_dir(lp, posixpath.join(dest_abs, lp.name))
        else:
            _upload_file(lp, dest_abs)

    return any_error


def detect_remote_shell(cli: SSHClient) -> str:
    try:
        code, out, _ = cli.run("command -v bash >/dev/null 2>&1 && echo bash || echo sh", timeout=5)
        if code == 0 and out.strip() == "sh":
            return "sh"
        return "bash"
    except Exception:
        return "bash"


def execute_remote_script(cli: SSHClient, sc: ServerConfig, remote_path: str, configured_port: int, actual_port: int, force_tty: bool, script_exit_on_error: bool, alias: str, script_args: Optional[List[str]] = None) -> int:
    # Prepare env
    env: Dict[str, str] = {
        "SSH_PORT": str(configured_port),
        "ACTUAL_PORT": str(actual_port),
        "ADMIN_USER": sc.user,
        "SSH_KEY_PATH": "",
        "SSH_PUBLIC_KEY": "",
    }
    keyfile = get_ssh_keyfile(sc)
    if keyfile:
        key_base = str(keyfile).removesuffix(".pub")
        env["SSH_KEY_PATH"] = key_base
        pub = Path(key_base + ".pub")
        if pub.exists():
            try:
                env["SSH_PUBLIC_KEY"] = pub.read_text(encoding="utf-8", errors="ignore")
                info("Including SSH public key for automatic setup")
            except Exception:
                pass

    shell = detect_remote_shell(cli)
    shell_opts = " -e" if script_exit_on_error else ""
    # chmod +x and execute; then remove the script file, propagate exit code
    args_part = ""
    if script_args:
        # Safely quote each argument for remote shell
        args_part = " " + " ".join(shlex.quote(x) for x in script_args)
    cmd = (
        f"cd ~ && chmod +x {shlex.quote(remote_path)} && "
        f"{shell}{shell_opts} {shlex.quote(remote_path)}{args_part} ; "
        f"rc=$?; rm -f {shlex.quote(remote_path)}; exit $rc"
    )

    # Execute, streaming output and logging lines to remote log
    assert cli.client is not None
    chan = cli.client.get_transport().open_session()
    if force_tty:
        chan.get_pty()
    for k, v in env.items():
        try:
            chan.update_environment({k: v})
        except Exception:
            pass
    chan.exec_command(cmd)

    # If TTY is requested, allow interactive stdin bridging
    if force_tty:
        if os.name == 'nt':
            import threading, msvcrt

            stop = False

            def reader():
                while not stop:
                    if chan.recv_ready():
                        data = chan.recv(32768)
                        if not data:
                            break
                        text = data.decode(errors="ignore")
                        for line in text.splitlines():
                            print(line)
                            import posixpath as _pp
                            log_remote(alias, _pp.basename(remote_path), line)
                    if chan.recv_stderr_ready():
                        data = chan.recv_stderr(32768)
                        if not data:
                            break
                        text = data.decode(errors="ignore")
                        for line in text.splitlines():
                            print(line)
                            import posixpath as _pp
                            log_remote(alias, _pp.basename(remote_path), line)
                    if chan.exit_status_ready():
                        break
                    time.sleep(0.01)

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            try:
                while True:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch == '\r':
                            chan.send("\r")
                        else:
                            try:
                                chan.send(ch.encode('utf-8'))
                            except Exception:
                                chan.send(ch)
                    if chan.exit_status_ready():
                        break
                    time.sleep(0.01)
            except KeyboardInterrupt:
                pass
            finally:
                stop = True
                rc = chan.recv_exit_status()
                try:
                    chan.close()
                except Exception:
                    pass
                return rc
        else:
            # POSIX: use select to multiplex stdin and channel
            try:
                import select as _select
                while True:
                    rlist, _, _ = _select.select([chan, sys.stdin], [], [])
                    if chan in rlist:
                        if chan.recv_ready():
                            data = chan.recv(32768)
                            if not data:
                                break
                            text = data.decode(errors="ignore")
                            for line in text.splitlines():
                                print(line)
                            import posixpath as _pp
                            log_remote(alias, _pp.basename(remote_path), line)
                        if chan.recv_stderr_ready():
                            data = chan.recv_stderr(32768)
                            if not data:
                                break
                            text = data.decode(errors="ignore")
                            for line in text.splitlines():
                                print(line)
                            import posixpath as _pp
                            log_remote(alias, _pp.basename(remote_path), line)
                    if sys.stdin in rlist:
                        try:
                            data = os.read(sys.stdin.fileno(), 1024)
                        except Exception:
                            data = b""
                        if data:
                            try:
                                chan.send(data)
                            except Exception:
                                # If channel closed while sending, exit loop
                                break
                    if chan.exit_status_ready():
                        # Drain remaining
                        while chan.recv_ready():
                            text = chan.recv(32768).decode(errors="ignore")
                            for line in text.splitlines():
                                print(line)
                                import posixpath as _pp
                                log_remote(alias, _pp.basename(remote_path), line)
                        while chan.recv_stderr_ready():
                            text = chan.recv_stderr(32768).decode(errors="ignore")
                            for line in text.splitlines():
                                print(line)
                                import posixpath as _pp
                                log_remote(alias, _pp.basename(remote_path), line)
                        break
            finally:
                rc = chan.recv_exit_status()
                try:
                    chan.close()
                except Exception:
                    pass
                return rc

    # Non-interactive mode: only read outputs
    try:
        while True:
            if chan.recv_ready():
                data = chan.recv(32768)
                if not data:
                    break
                text = data.decode(errors="ignore")
                for line in text.splitlines():
                    print(line)
                    log_remote(alias, remote_path.name, line)
            if chan.recv_stderr_ready():
                data = chan.recv_stderr(32768)
                if not data:
                    break
                text = data.decode(errors="ignore")
                for line in text.splitlines():
                    print(line)
                    log_remote(alias, remote_path.name, line)
            if chan.exit_status_ready():
                break
            time.sleep(0.01)
    finally:
        rc = chan.recv_exit_status()
        try:
            chan.close()
        except Exception:
            pass
    return rc


def run_scripts(sc: ServerConfig, gcfg: GlobalConfig, alias: str, script_args: Optional[List[str]] = None) -> int:
    if not sc.scripts:
        warn(f"No scripts defined for host-alias: {alias}")
        return 0

    print("")
    print("🚀 Starting script execution on server")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📍 Server: {sc.host}:{sc.port}")
    print(f"👤 User: {sc.user}")
    print(f"📜 Scripts: {', '.join(sc.scripts)}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("")

    total = len(sc.scripts)
    for idx, script_name in enumerate(sc.scripts, start=1):
        script_name = script_name.strip()
        print("")
        print(f"📋 [{idx}/{total}] Processing: {script_name}")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        use_port = determine_ssh_port(sc.port, sc.host)
        if not use_port:
            return 1

        local_script = SCRIPT_DIR / "scripts" / script_name
        if not local_script.exists():
            error(f"Script not found: {local_script}")
            return 1

        try:
            cli = SSHClient(sc.host, use_port, sc.user, sc.password, get_ssh_keyfile(sc))
            cli.connect(timeout=5)
        except Exception as e:
            error(f"SSH connection failed: {e}")
            return 1

        try:
            progress(f"Uploading {script_name} to server...")
            remote_path = upload_script(cli, local_script)
            success("Script uploaded successfully")

            print("")
            progress(f"Executing {script_name} on {sc.host}...")
            print("┌─ Remote Script Output ─────────────────────────────────┐")
            # Determine args for this script: CLI overrides per-script config, else default
            if script_args is not None and len(script_args) > 0:
                eff_args = script_args
            else:
                eff_args = sc.script_args_map.get(script_name) or sc.default_script_args

            rc = execute_remote_script(
                cli,
                sc,
                remote_path,
                configured_port=sc.port,
                actual_port=use_port,
                force_tty=gcfg.force_tty,
                script_exit_on_error=gcfg.script_exit_on_error,
                alias=alias,
                script_args=eff_args,
            )
            print("└─────────────────────────────────────────────────────────┘")
            print("")
            if rc != 0:
                error(f"Script execution failed: {script_name}")
                return rc
            success(f"Script executed successfully: {script_name}")
            if idx < total:
                info("Waiting 3 seconds before next script to avoid rate limiting...")
                time.sleep(3)
        finally:
            cli.close()

    print("")
    print("🏁 All scripts completed successfully!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    success(f"Script execution completed for {alias}")
    return 0


def run_single_script(sc: ServerConfig, gcfg: GlobalConfig, alias: str, script_name: str, script_args: Optional[List[str]] = None) -> int:
    use_port = determine_ssh_port(sc.port, sc.host)
    if not use_port:
        return 1

    local_script = SCRIPT_DIR / "scripts" / script_name
    if not local_script.exists():
        error(f"Script not found: {local_script}")
        print("")
        print(f"Available scripts in {SCRIPT_DIR/'scripts'}:")
        try:
            for p in sorted((SCRIPT_DIR / 'scripts').glob("*.sh")):
                print(f"  - {p.name}")
        except Exception:
            pass
        return 1

    try:
        cli = SSHClient(sc.host, use_port, sc.user, sc.password, get_ssh_keyfile(sc))
        cli.connect(timeout=5)
    except Exception as e:
        error(f"SSH connection failed: {e}")
        return 1

    try:
        progress(f"Uploading {script_name} to server...")
        remote_path = upload_script(cli, local_script)
        success("Script uploaded successfully")
        print("")
        progress(f"Executing script: {script_name} on {sc.host}")
        print("┌─ Remote Script Output ─────────────────────────────────┐")
        # Determine effective args: CLI overrides per-script config, else default
        if script_args is not None and len(script_args) > 0:
            eff_args = script_args
        else:
            eff_args = sc.script_args_map.get(script_name) or sc.default_script_args

        rc = execute_remote_script(
            cli,
            sc,
            remote_path,
            configured_port=sc.port,
            actual_port=use_port,
            force_tty=gcfg.force_tty,
            script_exit_on_error=gcfg.script_exit_on_error,
            alias=alias,
            script_args=eff_args,
        )
        print("└─────────────────────────────────────────────────────────┘")
        print("")
        if rc != 0:
            error(f"Script execution failed: {script_name}")
            return rc
        success(f"Script executed successfully: {script_name}")
        return 0
    finally:
        cli.close()


def list_scripts(sc: ServerConfig, alias: str) -> int:
    print("")
    print(f"Server: {sc.host}")
    print(f"Host-alias: {alias}")
    print("")
    if sc.scripts:
        print("Configured scripts (will run in this order):")
        for i, s in enumerate(sc.scripts, start=1):
            args_str = None
            if sc.script_args_map.get(s):
                args_str = " ".join(shlex.quote(x) for x in sc.script_args_map[s])
            elif sc.default_script_args:
                args_str = " ".join(shlex.quote(x) for x in sc.default_script_args)
            if args_str:
                print(f"  {i}. {s}    args: {args_str}")
            else:
                print(f"  {i}. {s}")
    else:
        print("No scripts configured for this host-alias.")
    print("")
    print(f"All available scripts in {SCRIPT_DIR/'scripts'}:")
    if (SCRIPT_DIR / "scripts").exists():
        any_found = False
        for p in sorted((SCRIPT_DIR / "scripts").glob("*.sh")):
            any_found = True
            print(f"  - {p.name}")
        if not any_found:
            print("  (no scripts found)")
    else:
        print("  (scripts directory not found)")
    print("")
    print(f"To run all configured scripts in order:\n  {SCRIPT_NAME} run-scripts {alias}")
    print("")
    print(f"To run a specific script:\n  {SCRIPT_NAME} run-script {alias} <script-name>")
    return 0


def show_logs(gcfg: GlobalConfig, log_type: str, lines: int) -> int:
    if not gcfg.enable_logging:
        warn("Logging is disabled")
        return 1
    log_file: Optional[Path] = None
    if log_type in {"main", "all"}:
        log_file = gcfg.log_dir / "sofilab.log"
        print(f"📋 Main Log (last {lines} lines):")
    elif log_type in {"error", "errors"}:
        log_file = gcfg.log_dir / "sofilab-error.log"
        print(f"❌ Error Log (last {lines} lines):")
    elif log_type == "remote":
        log_file = gcfg.log_dir / "sofilab-remote.log"
        print(f"🖥️  Remote Script Log (last {lines} lines):")
    else:
        error(f"Unknown log type: {log_type}")
        print("Available log types: main, error, remote")
        return 1

    if log_file.exists():
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        # Tail last N lines efficiently
        try:
            with log_file.open("rb") as f:
                data = tail_bytes(f, lines)
            sys.stdout.write(data.decode(errors="ignore"))
        except Exception:
            sys.stdout.write(log_file.read_text(encoding="utf-8", errors="ignore"))
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("")
        print(f"Log file location: {log_file}")
        try:
            print(f"Log file size: {human_size(log_file.stat().st_size)}")
        except Exception:
            pass
        return 0
    else:
        warn(f"Log file not found: {log_file}")
        try:
            print("Available log files in", gcfg.log_dir)
            for p in sorted(gcfg.log_dir.glob("*")):
                print("  ", p.name)
        except Exception:
            print("  (none)")
        return 1


def clear_logs(gcfg: GlobalConfig, log_type: str) -> int:
    if not gcfg.enable_logging:
        warn("Logging is disabled")
        return 1
    gcfg.log_dir.mkdir(parents=True, exist_ok=True)
    files: List[Path] = []
    if log_type == "main":
        files = [gcfg.log_dir / "sofilab.log"]
    elif log_type in {"error", "errors"}:
        files = [gcfg.log_dir / "sofilab-error.log"]
    elif log_type == "remote":
        files = [gcfg.log_dir / "sofilab-remote.log"]
    elif log_type == "all":
        files = [gcfg.log_dir / "sofilab.log", gcfg.log_dir / "sofilab-error.log", gcfg.log_dir / "sofilab-remote.log"]
    else:
        error(f"Unknown log type: {log_type}")
        return 1
    cleared = 0
    for f in files:
        try:
            f.write_text("")
            cleared += 1
        except Exception:
            pass
    if cleared:
        info(f"{('All logs' if log_type=='all' else log_type.title()+' log')} cleared")
        # Remove rotated logs if clearing all
        if log_type == "all":
            for base in ("sofilab.log", "sofilab-error.log", "sofilab-remote.log"):
                for r in gcfg.log_dir.glob(base + ".*"):
                    try:
                        r.unlink()
                        cleared += 1
                    except Exception:
                        pass
        return 0
    else:
        warn("Log file(s) not found")
        return 1


def reset_hostkey(sc: ServerConfig) -> int:
    # Remove entries from ~/.ssh/known_hosts for host and [host]:port
    kh = Path.home() / ".ssh" / "known_hosts"
    removed = False
    targets = {sc.host, f"[{sc.host}]:{sc.port}", "[{}]:{}".format(sc.host, 22)}
    if not kh.exists():
        warn("No known_hosts file found")
        return 1
    try:
        lines = kh.read_text(encoding="utf-8", errors="ignore").splitlines()
        new_lines = []
        for ln in lines:
            if any(t in ln for t in targets):
                removed = True
                continue
            new_lines.append(ln)
        if removed:
            kh.write_text("\n".join(new_lines) + ("\n" if new_lines else ""))
            info("✓ Host keys removed successfully")
            print("")
            print("You can now connect to the server without host key warnings:")
            print(f"  {SCRIPT_NAME} login {sc.aliases[0]}")
            return 0
        else:
            warn("No host keys found for this host/ports in known_hosts")
            print("The server might not be in your known_hosts file.")
            return 1
    except Exception as e:
        error(f"Failed to update known_hosts: {e}")
        return 1


def install_cli() -> int:
    # Always install the Python CLI as the main command
    if os.name == "nt":
        # Windows: create per-user wrappers, try multiple locations, and add to PATH
        try:
            created: List[Path] = []

            # Primary location: robust LocalAppData
            la = _win_local_appdata()
            primary_dir = (la if la else (Path.home() / "AppData" / "Local")) / "SofiLab" / "bin"

            py = sys.executable  # Use current Python interpreter
            script = str(SCRIPT_PATH)

            def write_wrappers(target_dir: Path) -> List[Path]:
                made: List[Path] = []
                target_dir.mkdir(parents=True, exist_ok=True)
                # .cmd wrapper
                w_cmd = target_dir / "sofilab.cmd"
                w_cmd.write_text("@echo off\r\n" f"\"{py}\" \"{script}\" %*\r\n", encoding="utf-8")
                made.append(w_cmd)
                # .bat wrapper (for some shells)
                w_bat = target_dir / "sofilab.bat"
                w_bat.write_text("@echo off\r\n" f"\"{py}\" \"{script}\" %*\r\n", encoding="utf-8")
                made.append(w_bat)
                # .ps1 wrapper (PowerShell-friendly shim)
                w_ps1 = target_dir / "sofilab.ps1"
                w_ps1.write_text(
                    "# SofiLab PowerShell shim\n"
                    "$ErrorActionPreference = 'Stop'\n"
                    f"& \"{py}\" \"{script}\" @args\n",
                    encoding="utf-8",
                )
                made.append(w_ps1)
                return made

            # Write wrappers only to the SofiLab user bin
            try:
                created.extend(write_wrappers(primary_dir))
            except Exception as e:
                warn(f"Could not write wrappers to {primary_dir}: {e}")

            # Verify at least one wrapper exists
            created = [p for p in created if p.exists()]
            if not created:
                # Final fallback: try explicit %USERPROFILE%\AppData\Local path
                try:
                    up_dir = Path.home() / "AppData" / "Local" / "SofiLab" / "bin"
                    created.extend(write_wrappers(up_dir))
                except Exception as e:
                    warn(f"Could not write wrappers to {up_dir}: {e}")
                created = [p for p in created if p.exists()]
                if not created:
                    error("Failed to create any wrapper files in user locations.")
                    return 1

            # Add primary_dir to PATH in HKCU if missing
            try:
                import winreg  # type: ignore
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as k:
                    try:
                        current_path, reg_type = winreg.QueryValueEx(k, "Path")
                    except FileNotFoundError:
                        current_path, reg_type = "", winreg.REG_EXPAND_SZ
                parts = [p for p in (current_path or "").split(";") if p]
                norm = lambda s: s.strip().lower().rstrip("\\/")
                norm_parts = [norm(p) for p in parts]
                target_norm = norm(str(primary_dir))

                # Build new PATH with SofiLab prepended, removing duplicates
                new_parts: List[str] = []
                new_parts.append(str(primary_dir))
                for p in parts:
                    if norm(p) == target_norm:
                        continue
                    new_parts.append(p)

                new_path = ";".join([s.strip().strip(';') for s in new_parts if s]).strip(';')
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
                    reg_type_out = reg_type if reg_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else winreg.REG_EXPAND_SZ
                    winreg.SetValueEx(k, "Path", 0, reg_type_out, new_path)
                try:
                    os.environ["PATH"] = new_path
                except Exception:
                    pass
                try:
                    import ctypes
                    HWND_BROADCAST = 0xFFFF
                    WM_SETTINGCHANGE = 0x001A
                    SMTO_ABORTIFHUNG = 0x0002
                    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None)
                except Exception:
                    pass
            except Exception:
                pass

            # Ensure PATHEXT contains .CMD and .BAT
            try:
                import winreg  # type: ignore
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as k:
                        user_pathext, pe_type = winreg.QueryValueEx(k, "PATHEXT")
                except FileNotFoundError:
                    pe_type = None
                    user_pathext = os.environ.get("PATHEXT", "")
                pathext_parts = [p.strip().strip('"').upper() for p in (user_pathext or "").split(";") if p]
                changed = False
                for ext in (".CMD", ".BAT"):
                    if ext not in pathext_parts:
                        pathext_parts.append(ext)
                        changed = True
                if changed:
                    new_pathext = ";".join(pathext_parts)
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
                        winreg.SetValueEx(k, "PATHEXT", 0, winreg.REG_EXPAND_SZ, new_pathext)
                    os.environ["PATHEXT"] = new_pathext
                    try:
                        import ctypes
                        HWND_BROADCAST = 0xFFFF
                        WM_SETTINGCHANGE = 0x001A
                        SMTO_ABORTIFHUNG = 0x0002
                        ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None)
                    except Exception:
                        pass
            except Exception:
                pass

            info("✓ Installation successful! IMPORTANT: fully close and reopen your terminal to use 'sofilab'.")
            # Print locations we wrote to
            unique_dirs = sorted({p.parent for p in created}, key=lambda p: str(p).lower())
            for d in unique_dirs:
                print(f"Installed wrappers in: {d}")
            print("If the command is not found, do one of the following:")
            print("- Reopen PowerShell/cmd and run: sofilab --version")
            print("- OR refresh this PowerShell session (copy-paste):")
            print("  $env:Path = [Environment]::GetEnvironmentVariable('Path','User') + ';' + [Environment]::GetEnvironmentVariable('Path','Machine'); $env:PATHEXT = ([Environment]::GetEnvironmentVariable('PATHEXT','User') + ';' + [Environment]::GetEnvironmentVariable('PATHEXT','Machine')); if (-not $env:PATHEXT.ToUpper().Contains('.CMD')) { $env:PATHEXT += ';.CMD;.BAT' }")
            print("- OR temporarily add just this folder in this session:")
            print("  $env:Path += ';' + \"$env:LOCALAPPDATA\\SofiLab\\bin\"; $env:PATHEXT += ';.CMD;.BAT'")
            print("You can also invoke explicitly once:")
            print("  & \"$env:LOCALAPPDATA\\SofiLab\\bin\\sofilab.cmd\" --version")
            print("PowerShell shim is also available:")
            print("  & \"$env:LOCALAPPDATA\\SofiLab\\bin\\sofilab.ps1\" --version")
            # Also show absolute path to one concrete wrapper we created
            try:
                first = sorted(created, key=lambda p: (str(p).lower()))[0]
                print(f"Direct wrapper path: {first}")
            except Exception:
                pass
            return 0
        except Exception as e:
            error(f"Windows installation failed: {e}")
            return 1
    # POSIX (macOS/Linux)
    system_bin = Path("/usr/local/bin")
    dest = system_bin / "sofilab"
    try:
        system_bin.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(SCRIPT_PATH)
        info("✓ Installation successful! 'sofilab' now points to the Python CLI.")
        return 0
    except PermissionError:
        # Fallback: install to user bin without sudo
        user_bin = Path.home() / ".local" / "bin"
        try:
            user_bin.mkdir(parents=True, exist_ok=True)
            user_dest = user_bin / "sofilab"
            if user_dest.is_symlink() or user_dest.exists():
                user_dest.unlink()
            try:
                user_dest.symlink_to(SCRIPT_PATH)
            except Exception:
                # If symlink not allowed, write a tiny wrapper script
                user_dest.write_text(
                    f"#!/usr/bin/env bash\nexec \"{sys.executable}\" \"{SCRIPT_PATH}\" \"$@\"\n",
                    encoding="utf-8",
                )
                os.chmod(user_dest, 0o755)
            info("✓ Installed to user bin: ~/.local/bin/sofilab")
            print("If not already in PATH, add it:")
            print("  export PATH=\"$HOME/.local/bin:$PATH\"")
            return 0
        except Exception as e:
            error(f"Failed to install to user bin: {e}")
            return 1
    except Exception as e:
        error(f"Failed to install: {e}")
        return 1


def uninstall_cli() -> int:
    if os.name == "nt":
        try:
            local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            install_dir = Path(local_app) / "SofiLab" / "bin"
            wrapper = install_dir / "sofilab.cmd"
            wrapper_bat = install_dir / "sofilab.bat"
            wrapper_ps1 = install_dir / "sofilab.ps1"
            if wrapper.exists():
                wrapper.unlink()
                info("Removed Windows wrapper sofilab.cmd")
            else:
                warn("No Windows wrapper found to remove")
            if wrapper_bat.exists():
                wrapper_bat.unlink()
                info("Removed Windows wrapper sofilab.bat")
            if wrapper_ps1.exists():
                wrapper_ps1.unlink()
                info("Removed Windows wrapper sofilab.ps1")
            # Attempt to remove path entry from HKCU if present
            try:
                import winreg  # type: ignore
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as k:
                    try:
                        current_path, reg_type = winreg.QueryValueEx(k, "Path")
                    except FileNotFoundError:
                        current_path, reg_type = "", winreg.REG_EXPAND_SZ
                parts = [p.strip() for p in current_path.split(";") if p.strip()]
                cleaned = [p for p in parts if Path(p).resolve().as_posix().lower().rstrip("/") != install_dir.resolve().as_posix().lower().rstrip("/")]
                if cleaned != parts:
                    new_path = ";".join(cleaned)
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
                        reg_type_out = reg_type if reg_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else winreg.REG_EXPAND_SZ
                        winreg.SetValueEx(k, "Path", 0, reg_type_out, new_path)
                    try:
                        os.environ["PATH"] = new_path
                    except Exception:
                        pass
                    try:
                        import ctypes
                        HWND_BROADCAST = 0xFFFF
                        WM_SETTINGCHANGE = 0x001A
                        SMTO_ABORTIFHUNG = 0x0002
                        ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None)
                    except Exception:
                        pass
            except Exception:
                pass
            return 0
        except Exception as e:
            error(f"Windows uninstallation failed: {e}")
            return 1
    # POSIX cleanup: system and user bins
    removed: List[Path] = []
    not_removed: List[Tuple[Path, str]] = []
    candidates = [Path("/usr/local/bin/sofilab"), Path.home() / ".local" / "bin" / "sofilab"]
    # Also scan PATH for any shims that point back to this repo's script
    try:
        path_dirs = [Path(p) for p in os.environ.get("PATH", "").split(":") if p]
        for d in path_dirs:
            p = d / "sofilab"
            if p in candidates:
                continue
            if p.exists():
                candidates.append(p)
    except Exception:
        pass

    for pth in candidates:
        try:
            if not (pth.exists() or pth.is_symlink()):
                continue
            # Determine if it's safe to remove
            remove_ok = False
            try:
                if pth.as_posix() in {"/usr/local/bin/sofilab", str((Path.home()/".local"/"bin"/"sofilab").as_posix())}:
                    # Known locations installed by us
                    remove_ok = True
                elif pth.is_symlink():
                    tgt = pth.resolve()
                    # Remove if it points to this script or to a sofilab.py in this repo
                    if tgt == SCRIPT_PATH or tgt.name == "sofilab.py":
                        remove_ok = True
                else:
                    # Inspect small wrappers for our script path
                    with pth.open("r", encoding="utf-8", errors="ignore") as f:
                        content = f.read(4096)
                        if "sofilab.py" in content or str(SCRIPT_PATH) in content:
                            remove_ok = True
            except Exception:
                pass

            if remove_ok:
                try:
                    pth.unlink()
                    removed.append(pth)
                except PermissionError:
                    not_removed.append((pth, "permission denied"))
                except Exception as e:
                    not_removed.append((pth, f"{e}"))
        except Exception:
            pass

    if removed:
        info("Uninstallation successful!")
        print("Removed these entries:")
        for p in removed:
            print(f"  - {p}")
        print("If your shell still finds 'sofilab', clear the command cache:")
        print("  bash/zsh: hash -r   or   re-open the terminal")
        # If some files could not be removed due to permissions, show guidance
        if not_removed:
            print("")
            print("The following entries could not be removed:")
            for p, why in not_removed:
                print(f"  - {p}  ({why})")
            print("Try removing them with elevated privileges:")
            print("  sudo rm -f /usr/local/bin/sofilab")
        return 0
    else:
        # Nothing removed; check if a root-owned /usr/local/bin/sofilab exists
        ul = Path("/usr/local/bin/sofilab")
        if ul.exists() or ul.is_symlink():
            warn("'sofilab' found in /usr/local/bin but cannot remove without sudo")
            print("Run this to complete uninstall:")
            print("  sudo rm -f /usr/local/bin/sofilab && hash -r")
            return 1
        warn("No installation found in standard locations")
        info("Nothing to uninstall")
        return 0


# --------------------------
# Doctor / Diagnostics
# --------------------------
def doctor_cli(repair_path: bool = False) -> int:
    """Diagnose installation on Windows and POSIX; repair wrappers on Windows."""
    print("")
    print("🔎 SofiLab Doctor")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Platform: {os.name}  Python: {sys.version.split()[0]}")
    print(f"Script: {SCRIPT_PATH}")

    if os.name == "nt":
        la = _win_local_appdata()
        primary_dir = ((la if la else (Path.home() / "AppData" / "Local"))) / "SofiLab" / "bin"
        paths = [primary_dir]
        try:
            import sysconfig
            sp = sysconfig.get_path("scripts")
            if sp:
                paths.append(Path(sp))
        except Exception:
            pass
        print("Candidate install directories:")
        for p in paths:
            print(f"  - {p}")

        found_any = False
        for p in paths:
            for name in ("sofilab.cmd", "sofilab.bat", "sofilab.ps1"):
                fp = p / name
                if fp.exists():
                    print(f"✓ Found: {fp}")
                    found_any = True
        if not found_any:
            print("⚠️  No wrappers found. Attempting to (re)create wrappers...")
            try:
                # Reuse installer logic to write wrappers
                install_cli()
            except Exception as e:
                error(f"Repair failed: {e}")
                return 1
            print("Re-run 'doctor' to confirm, or open a new shell and try 'sofilab --version'.")
            return 0

        print("")
        print("PATH entries containing SofiLab/Scripts:")
        for part in os.environ.get("PATH", "").split(";"):
            if "SofiLab\\bin" in part or part.lower().endswith("\\scripts"):
                print(f"  - {part}")

        # Analyze PATH for problematic segments (quotes/unicode/empties)
        print("")
        print("PATH sanity scan:")
        path_str = os.environ.get("PATH", "")
        segments = path_str.split(";")
        def has_unmatched_quote(s: str) -> bool:
            return s.count('"') % 2 != 0
        any_issue = False
        for idx, seg in enumerate(segments):
            s = seg.strip()
            if not s:
                continue
            issues = []
            if '"' in s:
                issues.append("contains quote(s)")
            if has_unmatched_quote(s):
                issues.append("unmatched quote")
            try:
                exists = Path(s.strip('"')).exists()
            except Exception:
                exists = False
            if not exists:
                issues.append("not found")
            if issues:
                any_issue = True
                print(f"  [{idx}] {s}  ->  {'; '.join(issues)}")
        if not any_issue:
            print("  (no obvious issues found)")

        if any_issue and repair_path:
            print("")
            print("Attempting PATH repair (user PATH only)...")
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as k:
                    try:
                        current_path, reg_type = winreg.QueryValueEx(k, "Path")
                    except FileNotFoundError:
                        current_path, reg_type = "", winreg.REG_EXPAND_SZ
                parts = [p for p in (current_path or "").split(";") if p]
                def clean(seg: str) -> str:
                    s = seg.strip().strip('"').strip()
                    # Collapse inner stray quotes
                    return s.replace('"', '')
                cleaned = [clean(p) for p in parts if clean(p)]
                # Deduplicate, preserve order
                seen = set()
                dedup: List[str] = []
                for p in cleaned:
                    key = p.lower().rstrip('\\/')
                    if key in seen:
                        continue
                    seen.add(key)
                    dedup.append(p)
                # Ensure SofiLab bin is first
                la = _win_local_appdata()
                sofidir = ((la if la else (Path.home() / "AppData" / "Local")) / "SofiLab" / "bin").resolve()
                sofikey = str(sofidir).lower().rstrip('\\/')
                dedup = [str(sofidir)] + [p for p in dedup if p.lower().rstrip('\\/') != sofikey]
                new_path = ";".join(dedup).strip(';')
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
                    reg_type_out = reg_type if reg_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else winreg.REG_EXPAND_SZ
                    winreg.SetValueEx(k, "Path", 0, reg_type_out, new_path)
                os.environ["PATH"] = new_path
                try:
                    import ctypes
                    HWND_BROADCAST = 0xFFFF
                    WM_SETTINGCHANGE = 0x001A
                    SMTO_ABORTIFHUNG = 0x0002
                    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None)
                except Exception:
                    pass
                print("✓ Rewrote user PATH to a sanitized value.")
                print("Open a new PowerShell/cmd and try: sofilab --version")
            except Exception as e:
                error(f"PATH repair failed: {e}")
        print("")
        print("PATHEXT:")
        print(f"  {os.environ.get('PATHEXT', '')}")
        print("")
        print("Try now:")
        print("  sofilab --version")
        return 0

    # POSIX
    print("Install locations checked:")
    for p in (Path("/usr/local/bin/sofilab"), Path.home() / ".local" / "bin" / "sofilab"):
        print(f"  - {p}: {'present' if p.exists() or p.is_symlink() else 'missing'}")
    print("Try: sofilab --version")
    return 0


# --------------------------
# Tailing helper
# --------------------------
def tail_bytes(fobj, n_lines: int, chunk_size: int = 4096) -> bytes:
    fobj.seek(0, os.SEEK_END)
    end = fobj.tell()
    size = end
    data = bytearray()
    lines = 0
    while end > 0 and lines <= n_lines:
        read_size = min(chunk_size, end)
        end -= read_size
        fobj.seek(end)
        buf = fobj.read(read_size)
        data[:0] = buf
        lines = data.count(b"\n")
        if end == 0:
            break
    # keep last n_lines
    parts = data.splitlines(keepends=True)
    tail = b"".join(parts[-n_lines:]) if n_lines < len(parts) else data
    return tail


# --------------------------
# CLI
# --------------------------
def usage_epilog() -> str:
    return f"""
Examples:
  {SCRIPT_NAME} login pmx
  {SCRIPT_NAME} login --hostname pmx
  {SCRIPT_NAME} reset-hostkey pmx
  {SCRIPT_NAME} run-scripts pmx --no-tty
  {SCRIPT_NAME} run-scripts --host-alias pmx --tty
  {SCRIPT_NAME} run-script pmx pmx-update-server.sh --tty
  {SCRIPT_NAME} run-script pmx error.sh --script-args "abc" "abd" 2 ls
  {SCRIPT_NAME} run-script pmx error.sh -- abc abd 2 ls
  {SCRIPT_NAME} run-script --host-alias pmx --script error.sh --tty --script-args abc abd
  {SCRIPT_NAME} status pmx
  {SCRIPT_NAME} status --hostname pmx
  {SCRIPT_NAME} reboot pmx --wait 180
  {SCRIPT_NAME} list-scripts pmx
  {SCRIPT_NAME} logs remote 100
  {SCRIPT_NAME} clear-logs remote
  {SCRIPT_NAME} install
  {SCRIPT_NAME} ls-remote pmx ~
  {SCRIPT_NAME} cp pmx:/var/log/syslog ./logs
  {SCRIPT_NAME} cp -r pmx:/etc/nginx ./backups
  {SCRIPT_NAME} cp ./local.txt pmx:~/uploads
  {SCRIPT_NAME} router-webui router enable
  {SCRIPT_NAME} router-webui --host-alias router disable
"""


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="SofiLab • Server Management Tool (Python)",
        epilog=usage_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    # Simple commands
    sub.add_parser("install")
    sub.add_parser("uninstall")
    # Diagnostics
    p_doc = sub.add_parser("doctor", help="Diagnose and repair installation issues on this system")
    p_doc.add_argument("--repair-path", action="store_true", help="Sanitize and rewrite the user PATH if issues are found")
    logs_p = sub.add_parser("logs")
    logs_p.add_argument("type", nargs="?", default="main")
    logs_p.add_argument("lines", nargs="?", type=int, default=50)
    clear_p = sub.add_parser("clear-logs")
    clear_p.add_argument("type", nargs="?", default="all")

    # Host based
    p_login = sub.add_parser("login")
    p_login.add_argument("alias", nargs="?")
    p_login.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_login.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")

    p_reset = sub.add_parser("reset-hostkey")
    p_reset.add_argument("alias", nargs="?")
    p_reset.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_reset.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")

    p_status = sub.add_parser("status")
    p_status.add_argument("alias", nargs="?")
    p_status.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_status.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")
    p_status.add_argument("--port", type=int)

    p_reboot = sub.add_parser("reboot")
    p_reboot.add_argument("alias", nargs="?")
    p_reboot.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_reboot.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")
    p_reboot.add_argument("--wait", nargs="?", const=180, type=int)

    p_list = sub.add_parser("list-scripts")
    p_list.add_argument("alias", nargs="?")
    p_list.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_list.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")

    p_runall = sub.add_parser("run-scripts")
    p_runall.add_argument("alias", nargs="?")
    p_runall.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_runall.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")
    p_runall.add_argument("--tty", dest="tty", action="store_true")
    p_runall.add_argument("--no-tty", dest="no_tty", action="store_true")

    p_runone = sub.add_parser("run-script")
    # Positional (backward compatible)
    p_runone.add_argument("alias", nargs="?")
    p_runone.add_argument("script", nargs="?")
    # Optional named variants (order-free)
    p_runone.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_runone.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")
    p_runone.add_argument("--script", dest="script_opt", help="Script name; alternative to positional")
    p_runone.add_argument("--tty", dest="tty", action="store_true")
    p_runone.add_argument("--no-tty", dest="no_tty", action="store_true")
    # Script arguments:
    #  - After a literal "--": captured by REMAINDER below
    #  - Or via --script-args with one or more values; stops at next option
    p_runone.add_argument("--script-args", dest="script_args_opt", nargs='+', help="Arguments passed to the script (quote as needed)")
    p_runone.add_argument("script_args", nargs=argparse.REMAINDER, help="Use after -- to pass raw script arguments")

    # Router utilities (ASUS Merlin)
    p_rwui = sub.add_parser("router-webui", help="Enable/disable ASUS Merlin remote web UI on router hosts")
    p_rwui.add_argument("alias", nargs="?")
    p_rwui.add_argument("action", choices=["enable", "disable"], help="Enable or disable the web UI over WAN")
    p_rwui.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_rwui.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")

    # File transfer
    p_ls = sub.add_parser("ls-remote")
    p_ls.add_argument("alias", nargs="?")
    p_ls.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_ls.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")
    p_ls.add_argument("path", nargs="?", default="~")

    p_dl = sub.add_parser("download")
    p_dl.add_argument("alias", nargs="?")
    p_dl.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_dl.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")
    p_dl.add_argument("remote", nargs="+", help="Remote file/dir path(s)")
    p_dl.add_argument("--dest", "-d", default=".", help="Local destination directory")
    p_dl.add_argument("-r", "--recursive", action="store_true")

    p_up = sub.add_parser("upload")
    p_up.add_argument("alias", nargs="?")
    p_up.add_argument("--host-alias", dest="alias_opt", help="Target host alias; alternative to positional")
    p_up.add_argument("--hostname", dest="alias_opt", help="Alias (compat)")
    p_up.add_argument("local", nargs="+", help="Local file/dir path(s)")
    p_up.add_argument("--dest", "-d", default="~", help="Remote destination directory")
    p_up.add_argument("-r", "--recursive", action="store_true")

    # Unified transfer (scp-like)
    p_cp = sub.add_parser("cp", help="Copy files between local and remote using alias:/path notation")
    p_cp.add_argument("src", nargs="+", help="Source path(s). Use alias:/path for remote")
    p_cp.add_argument("dest", help="Destination. Use alias:/path for remote")
    p_cp.add_argument("-r", "--recursive", action="store_true")

    # (speed test command removed for now)

    parser.add_argument("--version", "-V", action="store_true", help="Show version and exit")

    if not argv:
        parser.print_help()
        return 0

    # Parse config first for logging
    try:
        gcfg, servers = parse_conf(CONFIG_FILE)
    except FileNotFoundError:
        warn(f"Configuration file not found: {CONFIG_FILE}")
        # init logging with defaults so users still see logs if desired
        gcfg = GlobalConfig()
        servers = {}

    # CLI parse
    args = parser.parse_args(argv)

    if args.version:
        print("SofiLab Server Management Tool (Python)")
        print(f"Version: {VERSION}")
        print(f"Build: {BUILD_DATE}")
        print("Features: SSH connections, server monitoring, installation management")
        print(f"Author: {AUTHOR}")
        return 0

    # Init logging
    init_logging(gcfg)
    log.info("Command executed: %s %s", SCRIPT_NAME, " ".join(shlex.quote(a) for a in argv))
    log.info("Configuration loaded - LOG_DIR: %s, LOG_LEVEL: %s, ENABLE_LOGGING: %s", gcfg.log_dir, gcfg.log_level, gcfg.enable_logging)

    if args.cmd == "install":
        return install_cli()
    if args.cmd == "uninstall":
        return uninstall_cli()
    if args.cmd == "doctor":
        return doctor_cli(repair_path=getattr(args, "repair_path", False))
    if args.cmd == "logs":
        return show_logs(gcfg, args.type, args.lines)
    if args.cmd == "clear-logs":
        return clear_logs(gcfg, args.type)

    # Unified cp is handled before host-required commands
    if args.cmd == "cp":
        def _classify(ep: str):
            if ":" in ep:
                a, p = ep.split(":", 1)
                if a in servers:
                    return ("remote", a, p)
            return ("local", None, ep)

        srcs = [ _classify(s) for s in args.src ]
        dest_t = _classify(args.dest)

        src_remote_aliases = {a for t,a,_ in srcs if t == "remote"}
        src_has_remote = any(t == "remote" for t,_,_ in srcs)
        dest_is_remote = dest_t[0] == "remote"

        if src_has_remote and dest_is_remote:
            error("Remote-to-remote copy is not supported")
            return 1
        if not src_has_remote and not dest_is_remote:
            error("Local-to-local copy is not supported; at least one side must be remote")
            return 1
        if src_has_remote and len(src_remote_aliases) > 1:
            error("Sources span multiple remote aliases; copy one host at a time")
            return 1

        # Determine direction and alias
        if src_has_remote:
            # download: remote sources -> local dest
            alias = next(iter(src_remote_aliases))
            sc = servers.get(alias)
            if not sc:
                error(f"Unknown host-alias: {alias}")
                return 1
            port = determine_ssh_port(sc.port, sc.host)
            if not port:
                return 1
            keyfile = get_ssh_keyfile(sc)
            try:
                cli = SSHClient(sc.host, port, sc.user, sc.password, keyfile)
                cli.connect(timeout=5)
                remote_paths = [p for t,_,p in srcs if t == "remote"]
                local_dest = Path(dest_t[2]).expanduser().resolve()
                info("Note: use 'cp -r' to transfer directories recursively")
                return download_items(cli, remote_paths, local_dest, args.recursive)
            except Exception as e:
                error(f"SFTP error: {e}")
                return 1
            finally:
                try:
                    cli.close()  # type: ignore
                except Exception:
                    pass
        else:
            # upload: local sources -> remote dest
            alias = dest_t[1]
            sc = servers.get(alias) if alias else None
            if not sc:
                error(f"Unknown host-alias: {alias}")
                return 1
            port = determine_ssh_port(sc.port, sc.host)
            if not port:
                return 1
            keyfile = get_ssh_keyfile(sc)
            try:
                cli = SSHClient(sc.host, port, sc.user, sc.password, keyfile)
                cli.connect(timeout=5)
                local_paths = [Path(p).expanduser().resolve() for t,_,p in srcs if t == "local"]
                remote_dest = dest_t[2]
                info("Note: use 'cp -r' to transfer directories recursively")
                return upload_items(cli, local_paths, remote_dest, args.recursive)
            except Exception as e:
                error(f"SFTP error: {e}")
                return 1
            finally:
                try:
                    cli.close()  # type: ignore
                except Exception:
                    pass

    # Normalize flexible inputs for commands supporting alias options before checks
    if args.cmd in {"login", "reset-hostkey", "status", "reboot", "list-scripts", "run-scripts", "run-script", "ls-remote", "download", "upload", "router-webui"}:
        if getattr(args, "alias_opt", None):
            args.alias = args.alias_opt
        if args.cmd == "run-script" and getattr(args, "script_opt", None):
            args.script = args.script_opt

    # Host-required commands
    if not getattr(args, "alias", None):
        error("Host-alias required for this command")
        parser.print_help()
        return 1

    alias = args.alias
    sc = servers.get(alias)
    if not sc:
        error(f"Unknown host-alias: {alias}")
        return 1

    # Allow CLI to override TTY for run-scripts/run-script
    if hasattr(args, "tty") or hasattr(args, "no_tty"):
        if getattr(args, "tty", False):
            gcfg.force_tty = True
        if getattr(args, "no_tty", False):
            gcfg.force_tty = False

    if args.cmd == "login":
        return ssh_login(sc, alias)
    if args.cmd == "reset-hostkey":
        return reset_hostkey(sc)
    if args.cmd == "status":
        return server_status(sc, args.port)
    if args.cmd == "reboot":
        return reboot_server(sc, args.wait)
    if args.cmd == "list-scripts":
        return list_scripts(sc, alias)
    if args.cmd == "run-scripts":
        # Collect arguments to apply to each script from CLI (overrides config)
        script_args_cli: Optional[List[str]] = None
        if getattr(args, "script_args_opt", None):
            script_args_cli = args.script_args_opt
        elif getattr(args, "script_args", None):
            vals = args.script_args
            if vals and vals[0] == "--":
                vals = vals[1:]
            script_args_cli = vals
        return run_scripts(sc, gcfg, alias, script_args_cli)
    if args.cmd == "run-script":
        # Support both patterns: `--script-args ...` (stops at next option) or `--` remainder (must be last)
        script_args_cli: Optional[List[str]] = None
        if getattr(args, "script_args_opt", None):
            script_args_cli = args.script_args_opt
        elif getattr(args, "script_args", None):
            vals = args.script_args
            if vals and vals[0] == "--":
                vals = vals[1:]
            script_args_cli = vals
        return run_single_script(sc, gcfg, alias, args.script, script_args_cli)
    if args.cmd == "ls-remote":
        port = determine_ssh_port(sc.port, sc.host)
        if not port:
            return 1
        keyfile = get_ssh_keyfile(sc)
        try:
            cli = SSHClient(sc.host, port, sc.user, sc.password, keyfile)
            cli.connect(timeout=5)
            return sftp_list_directory(cli, args.path)
        except Exception as e:
            error(f"SFTP error: {e}")
            return 1
        finally:
            try:
                cli.close()  # type: ignore
            except Exception:
                pass
    # (test-speed handler removed for now)
    if args.cmd == "download":
        warn("'download' is deprecated. Use: sofilab cp alias:/path ... <local_dir>")
        port = determine_ssh_port(sc.port, sc.host)
        if not port:
            return 1
        keyfile = get_ssh_keyfile(sc)
        try:
            cli = SSHClient(sc.host, port, sc.user, sc.password, keyfile)
            cli.connect(timeout=5)
            dest = Path(args.dest).expanduser().resolve()
            return download_items(cli, args.remote, dest, args.recursive)
        except Exception as e:
            error(f"SFTP error: {e}")
            return 1
        finally:
            try:
                cli.close()  # type: ignore
            except Exception:
                pass
    if args.cmd == "upload":
        warn("'upload' is deprecated. Use: sofilab cp <local...> alias:/dest")
        port = determine_ssh_port(sc.port, sc.host)
        if not port:
            return 1
        keyfile = get_ssh_keyfile(sc)
        try:
            cli = SSHClient(sc.host, port, sc.user, sc.password, keyfile)
            cli.connect(timeout=5)
            locals_list = [Path(p).expanduser().resolve() for p in args.local]
            return upload_items(cli, locals_list, args.dest, args.recursive)
        except Exception as e:
            error(f"SFTP error: {e}")
            return 1
        finally:
            try:
                cli.close()  # type: ignore
            except Exception:
                pass

    if args.cmd == "router-webui":
        # Handled above for alias normalization and validation
        return router_webui(sc, args.action)

    error(f"Unknown command: {args.cmd}")
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

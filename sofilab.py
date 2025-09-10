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

# Paramiko is loaded lazily. If missing, we will auto-install from requirements.txt.
PARAMIKO_MOD = None  # type: ignore

def ensure_paramiko():
    global PARAMIKO_MOD
    if PARAMIKO_MOD is not None:
        return PARAMIKO_MOD
    try:
        PARAMIKO_MOD = importlib.import_module("paramiko")
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
            subprocess.check_call(cmd)
        except Exception as e:
            print(f"❌ Failed to install dependencies automatically: {e}", file=sys.stderr)
            print("Please run: pip install -r requirements.txt", file=sys.stderr)
            raise
        # Import again
        PARAMIKO_MOD = importlib.import_module("paramiko")
        return PARAMIKO_MOD

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

        sc = ServerConfig(section_aliases, host, user, password, port, keyfile, scripts)
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
        """Very simple interactive shell. Attempts to be cross-platform.
        """
        assert self.client is not None
        chan = self.client.invoke_shell(term=os.environ.get("TERM", "xterm"))

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
            # POSIX: select on stdin + channel
            try:
                while True:
                    rlist, _, _ = select.select([chan, sys.stdin], [], [])
                    if chan in rlist:
                        data = chan.recv(32768)
                        if not data:
                            break
                        sys.stdout.write(data.decode(errors="ignore"))
                        sys.stdout.flush()
                    if sys.stdin in rlist:
                        data = os.read(sys.stdin.fileno(), 1024)
                        if not data:
                            break
                        chan.send(data)
            except KeyboardInterrupt:
                pass
            finally:
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


def ssh_login(sc: ServerConfig) -> int:
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


def upload_script(cli: SSHClient, local_script: Path, remote_rel_dir: str = ".sofilab_scripts") -> Path:
    sftp = cli.sftp()
    # Ensure remote dir exists
    remote_dir = f"{remote_rel_dir}"
    try:
        sftp.stat(remote_dir)
    except IOError:
        sftp.mkdir(remote_dir)
    remote_path = f"{remote_dir}/{local_script.name}"
    sftp.put(str(local_script), remote_path)
    return Path(remote_path)


def detect_remote_shell(cli: SSHClient) -> str:
    try:
        code, out, _ = cli.run("command -v bash >/dev/null 2>&1 && echo bash || echo sh", timeout=5)
        if code == 0 and out.strip() == "sh":
            return "sh"
        return "bash"
    except Exception:
        return "bash"


def execute_remote_script(cli: SSHClient, sc: ServerConfig, remote_path: Path, configured_port: int, actual_port: int, force_tty: bool, script_exit_on_error: bool, alias: str) -> int:
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
    cmd = f"cd ~ && chmod +x {shlex.quote(str(remote_path))} && {shell}{shell_opts} {shlex.quote(str(remote_path))} ; rc=$?; rm -f {shlex.quote(str(remote_path))}; exit $rc"

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
                                log_remote(alias, remote_path.name, line)
                        if chan.recv_stderr_ready():
                            data = chan.recv_stderr(32768)
                            if not data:
                                break
                            text = data.decode(errors="ignore")
                            for line in text.splitlines():
                                print(line)
                                log_remote(alias, remote_path.name, line)
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
                                log_remote(alias, remote_path.name, line)
                        while chan.recv_stderr_ready():
                            text = chan.recv_stderr(32768).decode(errors="ignore")
                            for line in text.splitlines():
                                print(line)
                                log_remote(alias, remote_path.name, line)
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


def run_scripts(sc: ServerConfig, gcfg: GlobalConfig, alias: str) -> int:
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
            rc = execute_remote_script(
                cli,
                sc,
                remote_path,
                configured_port=sc.port,
                actual_port=use_port,
                force_tty=gcfg.force_tty,
                script_exit_on_error=gcfg.script_exit_on_error,
                alias=alias,
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


def run_single_script(sc: ServerConfig, gcfg: GlobalConfig, alias: str, script_name: str) -> int:
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
        rc = execute_remote_script(
            cli,
            sc,
            remote_path,
            configured_port=sc.port,
            actual_port=use_port,
            force_tty=gcfg.force_tty,
            script_exit_on_error=gcfg.script_exit_on_error,
            alias=alias,
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
        # Windows: create a per-user bin directory, write a wrapper, and add it to PATH
        try:
            local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            install_dir = Path(local_app) / "SofiLab" / "bin"
            install_dir.mkdir(parents=True, exist_ok=True)

            wrapper = install_dir / "sofilab.cmd"
            # Use the current Python executable to avoid relying on PATH
            py = sys.executable
            script = str(SCRIPT_PATH)
            wrapper.write_text(
                "@echo off\r\n"
                f"\"{py}\" \"{script}\" %*\r\n",
                encoding="utf-8"
            )

            # Add install_dir to the user's PATH in HKCU\Environment
            try:
                import winreg  # type: ignore
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as k:
                    try:
                        current_path, reg_type = winreg.QueryValueEx(k, "Path")
                    except FileNotFoundError:
                        current_path, reg_type = "", winreg.REG_EXPAND_SZ
                # Normalize and check membership
                parts = [p.strip() for p in current_path.split(";") if p.strip()]
                norm_parts = {p.lower().rstrip("\\/") for p in parts}
                norm_target = str(install_dir).lower().rstrip("\\/")
                if norm_target not in norm_parts:
                    new_path = (";".join(parts + [str(install_dir)])).strip(";")
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
                        # Preserve value type if possible
                        reg_type_out = reg_type if reg_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else winreg.REG_EXPAND_SZ
                        winreg.SetValueEx(k, "Path", 0, reg_type_out, new_path)
                    # Broadcast environment change so new consoles pick it up
                    try:
                        import ctypes
                        HWND_BROADCAST = 0xFFFF
                        WM_SETTINGCHANGE = 0x001A
                        SMTO_ABORTIFHUNG = 0x0002
                        ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0,
                                                                 "Environment", SMTO_ABORTIFHUNG, 5000, None)
                    except Exception:
                        pass
            except Exception:
                # Non-fatal if PATH update fails; wrapper still created
                pass

            info("✓ Installation successful! Open a new terminal to use 'sofilab'.")
            print(f"Installed wrapper: {wrapper}")
            print(f"Added to PATH (if needed): {install_dir}")
            return 0
        except Exception as e:
            error(f"Windows installation failed: {e}")
            return 1
    # POSIX
    install_dir = Path("/usr/local/bin")
    dest = install_dir / "sofilab"
    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(SCRIPT_PATH)
        info("✓ Installation successful! 'sofilab' now points to the Python CLI.")
        return 0
    except PermissionError:
        error("Insufficient permissions to create symlink in /usr/local/bin. Try with sudo or use manual usage.")
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
            if wrapper.exists():
                wrapper.unlink()
                info("Removed Windows wrapper sofilab.cmd")
            else:
                warn("No Windows wrapper found to remove")
            return 0
        except Exception as e:
            error(f"Windows uninstallation failed: {e}")
            return 1
    dest = Path("/usr/local/bin/sofilab")
    if dest.exists() or dest.is_symlink():
        try:
            dest.unlink()
            info("Uninstallation successful!")
            return 0
        except Exception as e:
            error(f"Failed to remove symlink: {e}")
            return 1
    else:
        warn("No installation found at /usr/local/bin/sofilab")
        info("Nothing to uninstall")
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
  {SCRIPT_NAME} reset-hostkey pmx
  {SCRIPT_NAME} run-scripts pmx --no-tty
  {SCRIPT_NAME} run-script pmx pmx-update-server.sh --tty
  {SCRIPT_NAME} status pmx
  {SCRIPT_NAME} reboot pmx --wait 180
  {SCRIPT_NAME} list-scripts pmx
  {SCRIPT_NAME} logs remote 100
  {SCRIPT_NAME} clear-logs remote
  {SCRIPT_NAME} install
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
    logs_p = sub.add_parser("logs")
    logs_p.add_argument("type", nargs="?", default="main")
    logs_p.add_argument("lines", nargs="?", type=int, default=50)
    clear_p = sub.add_parser("clear-logs")
    clear_p.add_argument("type", nargs="?", default="all")

    # Host based
    p_login = sub.add_parser("login")
    p_login.add_argument("alias")

    p_reset = sub.add_parser("reset-hostkey")
    p_reset.add_argument("alias")

    p_status = sub.add_parser("status")
    p_status.add_argument("alias")
    p_status.add_argument("--port", type=int)

    p_reboot = sub.add_parser("reboot")
    p_reboot.add_argument("alias")
    p_reboot.add_argument("--wait", nargs="?", const=180, type=int)

    p_list = sub.add_parser("list-scripts")
    p_list.add_argument("alias")

    p_runall = sub.add_parser("run-scripts")
    p_runall.add_argument("alias")
    p_runall.add_argument("--tty", dest="tty", action="store_true")
    p_runall.add_argument("--no-tty", dest="no_tty", action="store_true")

    p_runone = sub.add_parser("run-script")
    p_runone.add_argument("alias")
    p_runone.add_argument("script")
    p_runone.add_argument("--tty", dest="tty", action="store_true")
    p_runone.add_argument("--no-tty", dest="no_tty", action="store_true")

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
        return 0

    # Init logging
    init_logging(gcfg)
    log.info("Command executed: %s %s", SCRIPT_NAME, " ".join(shlex.quote(a) for a in argv))
    log.info("Configuration loaded - LOG_DIR: %s, LOG_LEVEL: %s, ENABLE_LOGGING: %s", gcfg.log_dir, gcfg.log_level, gcfg.enable_logging)

    if args.cmd == "install":
        return install_cli()
    if args.cmd == "uninstall":
        return uninstall_cli()
    if args.cmd == "logs":
        return show_logs(gcfg, args.type, args.lines)
    if args.cmd == "clear-logs":
        return clear_logs(gcfg, args.type)

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
        return ssh_login(sc)
    if args.cmd == "reset-hostkey":
        return reset_hostkey(sc)
    if args.cmd == "status":
        return server_status(sc, args.port)
    if args.cmd == "reboot":
        return reboot_server(sc, args.wait)
    if args.cmd == "list-scripts":
        return list_scripts(sc, alias)
    if args.cmd == "run-scripts":
        return run_scripts(sc, gcfg, alias)
    if args.cmd == "run-script":
        return run_single_script(sc, gcfg, alias, args.script)

    error(f"Unknown command: {args.cmd}")
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

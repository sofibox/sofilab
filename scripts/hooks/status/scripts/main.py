#!/usr/bin/env python3
import os, sys

def ensure_paramiko():
    try:
        import paramiko  # type: ignore
        return paramiko
    except Exception as e:
        print(f"Paramiko not available: {e}", file=sys.stderr)
        print("Install with: python -m pip install paramiko", file=sys.stderr)
        sys.exit(3)

def main() -> int:
    host = os.environ.get("SOFILAB_HOST")
    port = int(os.environ.get("SOFILAB_PORT", "22"))
    user = os.environ.get("SOFILAB_USER")
    key  = os.environ.get("SOFILAB_KEYFILE") or None
    pwd  = os.environ.get("SOFILAB_PASSWORD") or None
    alias = os.environ.get("SOFILAB_ALIAS") or ""

    if not host or not user:
        print("SOFILAB_HOST and SOFILAB_USER must be set by SofiLab", file=sys.stderr)
        return 2

    print(f"[status hook(py)] alias={alias} host={host} port={port} user={user}")

    paramiko = ensure_paramiko()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(hostname=host, port=port, username=user,
                    password=None if key else pwd,
                    key_filename=key,
                    look_for_keys=bool(key is None), allow_agent=True,
                    timeout=5, auth_timeout=5, banner_timeout=5)
    except Exception as e:
        print(f"SSH connection failed: {e}", file=sys.stderr)
        return 1

    try:
        for cmd in ("hostname", "uptime", "uname -sr"):
            try:
                stdin, stdout, stderr = cli.exec_command(cmd, timeout=5)
                out = stdout.read().decode("utf-8", errors="ignore").strip()
                err = stderr.read().decode("utf-8", errors="ignore").strip()
                line = out or err
                print(f"{cmd}: {line}")
            except Exception as e:
                print(f"{cmd}: error: {e}")
    finally:
        try:
            cli.close()
        except Exception:
            pass
    return 0

if __name__ == "__main__":
    sys.exit(main())


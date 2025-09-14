#!/usr/bin/env python3
import os, sys, time, socket, argparse

def check_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False

def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--mode", default="reboot")
    ap.add_argument("--wait", type=int, default=180)
    ap.add_argument("--down-timeout", dest="down_timeout", type=int, default=60)
    ap.add_argument("--diag", action="store_true")
    args, _ = ap.parse_known_args()

    host = os.environ.get("SOFILAB_HOST")
    port = int(os.environ.get("SOFILAB_PORT", "22"))
    user = os.environ.get("SOFILAB_USER")
    keyfile = os.environ.get("SOFILAB_KEYFILE") or None
    password = os.environ.get("SOFILAB_PASSWORD") or None
    alias = os.environ.get("SOFILAB_ALIAS") or ""

    if not host or not user:
        print("SOFILAB_HOST and SOFILAB_USER are required in environment", file=sys.stderr)
        return 2

    print(f"[reboot hook(py)] alias={alias} host={host} user={user} wait={args.wait}s", file=sys.stderr)

    if args.diag:
        print("[diag] check_port_open:", check_port_open(host, port), file=sys.stderr)
        return 0

    try:
        import paramiko
    except Exception as e:
        print(f"paramiko not available: {e}", file=sys.stderr)
        return 3

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(hostname=host, port=port, username=user,
                    password=None if keyfile else password,
                    key_filename=keyfile, look_for_keys=bool(keyfile is None),
                    allow_agent=True, timeout=5, auth_timeout=5, banner_timeout=5)
    except Exception as e:
        print(f"SSH connect failed: {e}", file=sys.stderr)
        return 4

    try:
        cmd = "systemctl reboot || reboot || shutdown -r now"
        try:
            # Exec without PTY; server will likely close connection during reboot
            chan = cli.get_transport().open_session()
            chan.exec_command(cmd)
            # Give command a moment to be accepted
            time.sleep(1.0)
        finally:
            try:
                cli.close()
            except Exception:
                pass

        # Wait for host to go down
        print(f"Waiting for {host}:{port} to go down...", file=sys.stderr)
        elapsed = 0
        while check_port_open(host, port):
            time.sleep(2)
            elapsed += 2
            if elapsed >= args.down_timeout:
                print(f"Still reachable after {args.down_timeout}s; continuing", file=sys.stderr)
                break

        # Wait for host to come back
        print(f"Waiting for host to come back (up to {args.wait}s)...", file=sys.stderr)
        elapsed = 0
        while not check_port_open(host, port):
            time.sleep(3)
            elapsed += 3
            if elapsed >= args.wait:
                print(f"Timeout waiting for {host}:{port} to return", file=sys.stderr)
                return 1
        print(f"Host is back online: {host}:{port}", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Reboot trigger failed: {e}", file=sys.stderr)
        return 5

if __name__ == "__main__":
    sys.exit(main())


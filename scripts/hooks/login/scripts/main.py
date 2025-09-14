#!/usr/bin/env python3
import os, sys, subprocess

def main() -> int:
    host = os.environ.get("SOFILAB_HOST")
    port = os.environ.get("SOFILAB_PORT", "22")
    user = os.environ.get("SOFILAB_USER")
    key  = os.environ.get("SOFILAB_KEYFILE") or ""
    ssh_bin = os.environ.get("SSH_BIN", "ssh")

    if not host or not user:
        print("SOFILAB_HOST and SOFILAB_USER must be set by SofiLab", file=sys.stderr)
        return 2

    cmd = [ssh_bin, "-p", str(port), "-o", "StrictHostKeyChecking=accept-new",
           "-o", f"UserKnownHostsFile={os.path.expanduser('~/.ssh/known_hosts')}"]
    if key and os.path.isfile(key):
        cmd += ["-i", key]
    cmd += [f"{user}@{host}"]

    # Replace current process with ssh for full TTY behavior
    os.execvp(cmd[0], cmd)
    return 0

if __name__ == "__main__":
    sys.exit(main())


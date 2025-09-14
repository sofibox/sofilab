#!/usr/bin/env bash
set -euo pipefail

# Example status hook for SofiLab
# Env vars provided:
#   SOFILAB_HOST, SOFILAB_PORT, SOFILAB_USER, SOFILAB_PASSWORD, SOFILAB_KEYFILE, SOFILAB_ALIAS

echo "[SofiLab status hook] alias=${SOFILAB_ALIAS} host=${SOFILAB_HOST} port=${SOFILAB_PORT} user=${SOFILAB_USER}" >&2

# Minimal check: show reachability and basic remote info using ssh
key_opts=()
[[ -n "${SOFILAB_KEYFILE:-}" && -f "$SOFILAB_KEYFILE" ]] && key_opts=(-i "$SOFILAB_KEYFILE")

ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
    -p "$SOFILAB_PORT" "${key_opts[@]}" "${SOFILAB_USER}@${SOFILAB_HOST}" \
    'echo -n "Hostname: "; hostname; echo -n "Uptime: "; uptime; echo -n "Kernel: "; uname -sr' || {
  echo "Unable to connect via SSH or command failed" >&2
  exit 1
}

exit 0


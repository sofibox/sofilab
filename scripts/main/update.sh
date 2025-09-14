#!/usr/bin/env bash
set -euo pipefail

echo "[update.sh] Running on $(hostname) as $(whoami)"
echo "[update.sh] Args: $*"
echo "[update.sh] SofiLab context: alias=${SOFILAB_ALIAS:-} host=${SOFILAB_HOST:-} user=${SOFILAB_USER:-}"

# Safe demo: print basic system info
uname -a || true
uptime || true

exit 0


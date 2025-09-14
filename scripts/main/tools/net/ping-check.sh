#!/usr/bin/env bash
set -euo pipefail

COUNT=3
TARGET="${1:-8.8.8.8}"
shift || true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --count|-c) COUNT="${2:-3}"; shift 2;;
    --) shift; break;;
    *) break;;
  esac
done

echo "[ping-check] Pinging $TARGET ($COUNT times)"
if command -v ping >/dev/null 2>&1; then
  ping -c "$COUNT" "$TARGET" || true
else
  echo "ping not available on this host"
fi

exit 0


#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# SofiLab login hook (POSIX)
# Uses env vars provided by SofiLab. Falls back to prompting for password if needed.

: "${SOFILAB_HOST:?SOFILAB_HOST not set}"
: "${SOFILAB_PORT:?SOFILAB_PORT not set}"
: "${SOFILAB_USER:?SOFILAB_USER not set}"

KEY_OPTS=()
if [[ -n "${SOFILAB_KEYFILE:-}" && -f "${SOFILAB_KEYFILE}" ]]; then
  KEY_OPTS=(-i "${SOFILAB_KEYFILE}")
fi

SSH_BIN="${SSH_BIN:-ssh}"

exec "${SSH_BIN}" \
  -p "${SOFILAB_PORT}" \
  -o StrictHostKeyChecking=accept-new \
  -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
  "${KEY_OPTS[@]}" \
  "${SOFILAB_USER}@${SOFILAB_HOST}" \
  "$@"


#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# SofiLab status hook (preferred layout)
# Works standalone or via SofiLab. CLI flags override env when both provided.

HOST=${SOFILAB_HOST:-}
USER=${SOFILAB_USER:-}
PORT=${SOFILAB_PORT:-22}
KEY=${SOFILAB_KEYFILE:-}
ALIAS=${SOFILAB_ALIAS:-}
SSH_BIN=${SSH_BIN:-ssh}
CMD=""

print_usage() {
  cat <<USAGE
Usage: ${0##*/} [--host HOST] [--user USER] [--port PORT] [--key PATH] [--alias NAME] [--cmd "remote shell"]

Examples:
  ${0##*/} --host 1.2.3.4 --user root --cmd "hostname; uptime; uname -sr"
  SOFILAB_HOST=1.2.3.4 SOFILAB_USER=root ${0##*/}

Environment variables (set by SofiLab when invoked as a hook):
  SOFILAB_HOST, SOFILAB_USER, SOFILAB_PORT, SOFILAB_KEYFILE, SOFILAB_ALIAS
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2;;
    --user) USER="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --key)  KEY="$2"; shift 2;;
    --alias) ALIAS="$2"; shift 2;;
    --cmd) CMD="$2"; shift 2;;
    -h|--help) print_usage; exit 0;;
    --) shift; break;;
    *) break;;
  esac
done

if [[ -z "${HOST}" || -z "${USER}" ]]; then
  echo "Missing --host/--user (or SOFILAB_HOST/SOFILAB_USER)" >&2
  print_usage
  exit 2
fi

KEY_OPTS=()
if [[ -n "${KEY}" && -f "${KEY}" ]]; then
  KEY_OPTS=(-i "${KEY}")
fi

if [[ -z "${CMD}" ]]; then
  # Default set of quick checks
  # Send via here-doc to avoid quoting issues
  exec "${SSH_BIN}" -p "${PORT}" "${KEY_OPTS[@]}" -o StrictHostKeyChecking=accept-new \
    "${USER}@${HOST}" sh -s <<'REMOTE'
set -e
printf 'Hostname: '; hostname
printf 'Uptime:   '; uptime
printf 'Kernel:   '; uname -sr
REMOTE
else
  # Run provided remote command under bash -lc
  exec "${SSH_BIN}" -p "${PORT}" "${KEY_OPTS[@]}" -o StrictHostKeyChecking=accept-new \
    "${USER}@${HOST}" bash -lc "$CMD"
fi


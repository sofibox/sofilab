#!/usr/bin/env bash
set -euo pipefail

# Example dynamic hook: rbl-scanner
# Usage: sofilab rbl-scanner <alias> [domain] [-- extra args]

echo "[SofiLab rbl-scanner hook] alias=${SOFILAB_ALIAS} host=${SOFILAB_HOST}" >&2

domain=${1:-}
if [[ -z "$domain" || "$domain" == --* ]]; then
  echo "Usage: sofilab rbl-scanner <alias> <domain>" >&2
  exit 2
fi

# Placeholder scanner: query a few DNSBLs via remote host
BLs=( "zen.spamhaus.org" "bl.spamcop.net" "dnsbl.sorbs.net" )

ssh_args=( -o BatchMode=yes -o StrictHostKeyChecking=accept-new -p "$SOFILAB_PORT" )
[[ -n "${SOFILAB_KEYFILE:-}" && -f "$SOFILAB_KEYFILE" ]] && ssh_args+=( -i "$SOFILAB_KEYFILE" )

remote_cmd='set -e; d="$1"; echo "Testing $d"; for bl in "$@"; do echo -n "$bl: "; host "$d" "$bl" >/dev/null 2>&1 && echo LISTED || echo OK; done'

ssh "${ssh_args[@]}" "${SOFILAB_USER}@${SOFILAB_HOST}" bash -lc "$remote_cmd" -- "$domain" "${BLs[@]}"


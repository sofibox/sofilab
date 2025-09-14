#!/usr/bin/env sh
set -eu

AIDE_BIN=$(command -v aide || true)
CONF="/etc/aide/aide.conf"
ACTION="--check"

if [ -z "$AIDE_BIN" ]; then
  echo "ERROR: aide not found" >&2
  exit 1
fi

# Accept a simple --update flag to update DB after scan
if [ "${1-}" = "--update" ]; then
  ACTION="--update"
fi

echo "Running: $AIDE_BIN $ACTION -c $CONF"
"$AIDE_BIN" "$ACTION" -c "$CONF"
rc=$?
if [ $rc -eq 0 ]; then
  echo "OK: AIDE reported no changes"
else
  echo "AIDE exit code: $rc (non-zero means changes or warnings)"
fi
exit $rc

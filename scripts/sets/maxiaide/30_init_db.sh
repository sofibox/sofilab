#!/usr/bin/env sh
set -eu

AIDE_BIN=$(command -v aide || true)
AIDE_INIT_BIN=$(command -v aideinit || true)
CONF="/etc/aide/aide.conf"

if [ -n "$AIDE_INIT_BIN" ]; then
  echo "Initializing AIDE database via aideinit ..."
  "$AIDE_INIT_BIN" -y -c "$CONF" || exit 1
else
  if [ -z "$AIDE_BIN" ]; then
    echo "ERROR: aide not found" >&2
    exit 1
  fi
  echo "Initializing AIDE database via 'aide --init' ..."
  "$AIDE_BIN" --init -c "$CONF" || exit 1
fi

DB_NEW="/var/lib/aide/aide.db.new"
DB="/var/lib/aide/aide.db"
if [ -f "$DB_NEW" ]; then
  mv -f "$DB_NEW" "$DB"
  echo "OK: Initialized database copied to $DB"
else
  echo "WARN: Expected $DB_NEW not found; check AIDE output" >&2
fi

exit 0

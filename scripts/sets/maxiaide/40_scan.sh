#!/usr/bin/env sh
set -eu

AIDE_BIN=$(command -v aide || true)
CONF="/etc/aide/aide.conf"
ACTION="--check"
DB_DIR="/var/lib/aide"
DB_NEW="$DB_DIR/aide.db.new"
DB="$DB_DIR/aide.db"

if [ -z "$AIDE_BIN" ]; then
  echo "ERROR: aide not found" >&2
  exit 1
fi

# Accept a simple --update flag to update DB after scan
if [ "${1-}" = "--update" ]; then
  ACTION="--update"
fi

echo "Running: $AIDE_BIN $ACTION -c $CONF"
set +e
"$AIDE_BIN" "$ACTION" -c "$CONF"
rc=$?
set -e

# When updating, move the new DB into place if produced
if [ "$ACTION" = "--update" ] && [ -f "$DB_NEW" ]; then
  if [ -f "$DB" ]; then
    mv -f "$DB" "$DB.$(date +%Y%m%d%H%M%S).bak" || true
  fi
  mv -f "$DB_NEW" "$DB"
  echo "OK: Updated AIDE database to $DB"
fi

if [ $rc -eq 0 ]; then
  echo "OK: AIDE reported no changes"
else
  echo "AIDE exit code: $rc (non-zero means changes or warnings)"
fi
exit $rc

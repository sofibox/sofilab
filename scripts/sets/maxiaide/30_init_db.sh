#!/usr/bin/env sh
set -eu

AIDE_BIN=$(command -v aide || true)
AIDE_INIT_BIN=$(command -v aideinit || true)
CONF="/etc/aide/aide.conf"
DB_DIR="/var/lib/aide"
DB_NEW="$DB_DIR/aide.db.new"
DB="$DB_DIR/aide.db"

if [ -z "$AIDE_BIN" ] && [ -z "$AIDE_INIT_BIN" ]; then
  echo "ERROR: aide not found; ensure prerequisites step installed it" >&2
  exit 1
fi

# Ensure runtime dirs exist with sane perms (some distros require this)
mkdir -p /run/aide || true
chmod 700 /run/aide 2>/dev/null || true
chown root:root /run/aide 2>/dev/null || true
mkdir -p "$DB_DIR" || true
chown root:root "$DB_DIR" 2>/dev/null || true

# Idempotent: if DB already exists, skip init
if [ -f "$DB" ]; then
  echo "OK: AIDE database already present at $DB; skipping initialization"
  exit 0
fi

echo "Initializing AIDE database ..."
rc=0
if [ -n "$AIDE_INIT_BIN" ]; then
  "$AIDE_INIT_BIN" -y -c "$CONF" || rc=$?
else
  "$AIDE_BIN" --init -c "$CONF" || rc=$?
fi

# Some AIDE versions return non-zero even when DB is produced; trust DB presence
if [ -f "$DB_NEW" ]; then
  mv -f "$DB_NEW" "$DB"
  echo "OK: Initialized database copied to $DB (init rc=$rc)"
  exit 0
fi

echo "ERROR: Initialization failed (rc=$rc) and no $DB_NEW produced" >&2
exit 1

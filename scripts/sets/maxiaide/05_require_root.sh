#!/usr/bin/env sh
set -eu

if [ "$(id -u)" != "0" ]; then
  echo "ERROR: This step must run as root." >&2
  exit 1
fi

echo "OK: Running as root"
exit 0


#!/usr/bin/env sh
set -eu

# Router requirement check (ASUSWRT/Merlin expected)
# Aborts this set if the target is not an ASUS router running ASUSWRT/Merlin.

is_asus=false

# Primary: nvram tool with productid/buildno
if command -v nvram >/dev/null 2>&1; then
  pid="$(nvram get productid 2>/dev/null || true)"
  build="$(nvram get buildno 2>/dev/null || true)"
  if [ -n "$pid" ] || [ -n "$build" ]; then
    is_asus=true
    echo "ASUSWRT detected: productid=${pid:-unknown} buildno=${build:-unknown}"
  fi
fi

# Secondary: /jffs usually present on ASUSWRT/Merlin
if [ "$is_asus" = false ] && [ -d /jffs ]; then
  is_asus=true
  echo "ASUSWRT detected via /jffs mount"
fi

# Tertiary: service helper exists; httpd restart target commonly available
if [ "$is_asus" = false ] && command -v service >/dev/null 2>&1; then
  # Avoid executing service actions; just note presence of helper
  is_asus=true
  echo "ASUSWRT likely (service helper present)"
fi

if [ "$is_asus" = false ]; then
  echo "ERROR: This script set (router-scripts) must run on an ASUSWRT/Merlin router. Aborting." >&2
  echo "Hint: remove or rename this check if you intentionally target a different device." >&2
  exit 1
fi

exit 0


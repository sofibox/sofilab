#!/usr/bin/env sh
set -eu

# Proxmox requirement check
# This step aborts the set if the target is not a Proxmox VE host.

is_proxmox=false

# Fast path: Proxmox provides the pveversion tool
if command -v pveversion >/dev/null 2>&1; then
  is_proxmox=true
  ver="$(pveversion 2>/dev/null | head -n1 || true)"
  [ -n "$ver" ] && echo "Proxmox detected: $ver" || echo "Proxmox detected (pveversion present)"
fi

# Fallback: presence of /etc/pve (PVE cluster filesystem)
if [ "$is_proxmox" = false ] && [ -d /etc/pve ]; then
  is_proxmox=true
  echo "Proxmox detected via /etc/pve"
fi

# Optional: check for common PVE services
if [ "$is_proxmox" = false ] && command -v systemctl >/dev/null 2>&1; then
  if systemctl status pveproxy >/dev/null 2>&1 || systemctl status pvedaemon >/dev/null 2>&1; then
    is_proxmox=true
    echo "Proxmox detected via pve services"
  fi
fi

if [ "$is_proxmox" = false ]; then
  echo "ERROR: This script set (proxmox-scripts) must be run on a Proxmox VE host. Aborting." >&2
  echo "Hint: remove or rename this check if you intentionally target a non-PVE host." >&2
  exit 1
fi

exit 0


#!/bin/bash
# Proxmox Repository Setup and System Update
# - Detects Debian codename and Proxmox version
# - Uses deb822 sources with explicit Signed-By (no notices)
# - Fixes locale warnings (e.g., LC_CTYPE=UTF-8) via safe defaults + sanitizer
# - Idempotent: safe to re-run

set -euo pipefail

# =========================
# Functions (top for clarity)
# =========================

log() { printf '%s\n' "$*"; }

disable_enterprise_repos() {
  log "Step 0a: Disable enterprise repositories to avoid 401 errors..."
  # Disable enterprise repos by commenting them out (safer than deletion)
  if [ -f /etc/apt/sources.list.d/pve-enterprise.list ]; then
    sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/pve-enterprise.list
    log "Disabled pve-enterprise.list"
  fi
  if [ -f /etc/apt/sources.list.d/ceph.list ]; then
    sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/ceph.list
    log "Disabled ceph.list"
  fi
  # Also check for .sources files
  if [ -f /etc/apt/sources.list.d/pve-enterprise.sources ]; then
    mv /etc/apt/sources.list.d/pve-enterprise.sources /etc/apt/sources.list.d/pve-enterprise.sources.disabled
    log "Disabled pve-enterprise.sources"
  fi
  if [ -f /etc/apt/sources.list.d/ceph.sources ]; then
    mv /etc/apt/sources.list.d/ceph.sources /etc/apt/sources.list.d/ceph.sources.disabled
    log "Disabled ceph.sources"
  fi
  echo
}

ensure_locales_configured() {
  log "Step 0b: Configure locales..."
  apt-get update -y
  apt-get install -y locales

  # Ensure en_US.UTF-8 is enabled and generated (present on most systems)
  sed -i 's/^# *\(en_US\.UTF-8 UTF-8\)/\1/' /etc/locale.gen
  grep -q 'en_US.UTF-8 UTF-8' /etc/locale.gen || echo 'en_US.UTF-8 UTF-8' >> /etc/locale.gen
  locale-gen

  # Safe server default: C.UTF-8 (available without a full language pack)
  update-locale LANG=C.UTF-8 LC_ALL=C.UTF-8
  log "Locales ready: default=C.UTF-8, en_US.UTF-8 generated."
  echo
}

install_login_locale_sanitizer() {
  # Normalizes bad values sent by some SSH clients (e.g., LC_CTYPE=UTF-8)
  local sanitizer=/etc/profile.d/00-locale-sanitize.sh
  if [ ! -f "$sanitizer" ]; then
    cat >"$sanitizer" <<'EOF'
# Normalize invalid or empty locale environment values at login.
# This prevents warnings like: "setlocale: LC_CTYPE: cannot change locale (UTF-8)"
normalize_locale_var() {
  local name="$1"
  local value="${!name-}"
  if [ "$value" = "UTF-8" ] || [ -z "${value:-}" ]; then
    export "$name"="C.UTF-8"
  fi
}
# Normalize common variables
normalize_locale_var LANG
normalize_locale_var LC_ALL
normalize_locale_var LC_CTYPE
unset -f normalize_locale_var
EOF
    chmod 644 "$sanitizer"
    log "Installed login-time locale sanitizer: $sanitizer"
  else
    log "Locale sanitizer already present."
  fi
  echo
}

detect_debian_and_pve() {
  # shellcheck disable=SC1091
  . /etc/os-release
  DEB_CODENAME="${VERSION_CODENAME:-unknown}"

  PVE_MANAGER_VER="$(pveversion -v 2>/dev/null | awk -F'[/ ]' '/^pve-manager/ {print $2}' || true)"
  PVE_MAJOR="${PVE_MANAGER_VER%%.*}"

  case "$DEB_CODENAME" in
    trixie)   EXPECTED_PVE="9"; SUITE="trixie" ;;
    bookworm) EXPECTED_PVE="8"; SUITE="bookworm" ;;
    *) log "Unsupported Debian codename: $DEB_CODENAME"; exit 1 ;;
  esac

  if [ -n "${PVE_MAJOR:-}" ] && [ "$PVE_MAJOR" != "$EXPECTED_PVE" ]; then
    log "Mismatch: Proxmox $PVE_MAJOR.x on Debian $DEB_CODENAME (expected PVE $EXPECTED_PVE.x)."
    exit 1
  fi

  log "Detected Debian: $DEB_CODENAME${PVE_MANAGER_VER:+, Proxmox $PVE_MANAGER_VER}"
  echo
}

ensure_keyrings_present() {
  log "Step 1: Ensure APT keyrings are present..."
  DEBIAN_KEYRING="/usr/share/keyrings/debian-archive-keyring.gpg"
  PROXMOX_KEYRING="/usr/share/keyrings/proxmox-archive-keyring.gpg"

  if [ ! -s "$DEBIAN_KEYRING" ]; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y debian-archive-keyring
  fi

  if [ ! -s "$PROXMOX_KEYRING" ]; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y proxmox-archive-keyring || true
    if [ ! -s "$PROXMOX_KEYRING" ]; then
      log "WARNING: proxmox-archive-keyring not found; you may see Signed-By notices until installed."
    fi
  fi
  echo
}

write_debian_deb822_sources() {
  cat >/etc/apt/sources.list.d/debian.sources <<EOF
Types: deb
URIs: http://deb.debian.org/debian
Suites: ${DEB_CODENAME} ${DEB_CODENAME}-updates
Components: main contrib non-free-firmware
Signed-By: ${DEBIAN_KEYRING}

Types: deb
URIs: http://security.debian.org/debian-security
Suites: ${DEB_CODENAME}-security
Components: main contrib non-free-firmware
Signed-By: ${DEBIAN_KEYRING}
EOF
}

write_proxmox_deb822_sources() {
  cat >/etc/apt/sources.list.d/proxmox.sources <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: ${SUITE}
Components: pve-no-subscription
Signed-By: ${PROXMOX_KEYRING}
EOF
}

configure_repositories() {
  log "Step 2: Configure repositories (deb822 + Signed-By)..."

  # Remove enterprise/ceph and legacy sources
  rm -f /etc/apt/sources.list.d/pve-enterprise.list \
        /etc/apt/sources.list.d/pve-enterprise.sources \
        /etc/apt/sources.list.d/ceph.list \
        /etc/apt/sources.list.d/ceph.sources

  # Remove legacy Debian list; we will write deb822 fresh
  rm -f /etc/apt/sources.list
  rm -f /etc/apt/sources.list.d/debian.sources

  # Remove old Proxmox .list to avoid duplicates
  rm -f /etc/apt/sources.list.d/pve-no-subscription.list

  write_debian_deb822_sources
  write_proxmox_deb822_sources

  log "Repositories configured."
  echo
}

refresh_and_upgrade_system() {
  log "Step 3: Refresh & upgrade packages..."
  apt-get clean
  rm -rf /var/lib/apt/lists/*
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get -y full-upgrade
  echo
}

install_extra_packages() {
  log "Step 4: Install extra useful packages..."
  apt-get install -y htop btop
  echo
}

# =========================
# Main
# =========================

log "=== Proxmox Repository Setup and System Update ==="
log "Date: $(date)"
echo

disable_enterprise_repos
ensure_locales_configured
install_login_locale_sanitizer
detect_debian_and_pve
ensure_keyrings_present
configure_repositories
refresh_and_upgrade_system
install_extra_packages

log "=== Complete ==="
log "Debian codename : $DEB_CODENAME"
[ -n "${PVE_MANAGER_VER:-}" ] && log "Proxmox version : $PVE_MANAGER_VER"
log "Repos set to    : Debian (${SUITE}) + Proxmox no-subscription (deb822 with Signed-By)"
log "Locale default  : C.UTF-8 (en_US.UTF-8 generated)"
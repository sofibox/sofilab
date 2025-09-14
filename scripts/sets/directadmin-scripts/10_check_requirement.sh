#!/usr/bin/env sh
set -eu

# DirectAdmin installation requirement check
# - Verifies OS family/version, architecture, root privileges
# - Ensures basic tools are present (curl, wget, tar, perl, dig)
# - Verifies FQDN hostname
# - Writes discovered info to /root/directadmin.env for later steps

ok=true

log() { printf '%s\n' "$*"; }
err() { printf 'ERROR: %s\n' "$*" >&2; ok=false; }
warn() { printf 'WARN: %s\n' "$*" >&2; }

# 1) Must be root
if [ "$(id -u)" != "0" ]; then
  err "This step must run as root"
fi

# 2) Detect OS
OS_ID=""; OS_VER=""; OS_LIKE=""; NAME=""
if [ -f /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID=${ID:-}
  OS_LIKE=${ID_LIKE:-}
  OS_VER=${VERSION_ID:-}
  NAME=${NAME:-}
else
  # Fallbacks
  if [ -f /etc/centos-release ] || [ -f /etc/redhat-release ]; then
    OS_ID="rhel"; OS_VER=""; NAME="RHEL-like"
  elif [ -f /etc/debian_version ]; then
    OS_ID="debian"; OS_VER=""; NAME="Debian-like"
  fi
fi

arch=$(uname -m 2>/dev/null || echo unknown)
case "$arch" in
  x86_64|amd64) : ;; 
  *) err "Unsupported architecture: $arch (DirectAdmin typically requires x86_64)" ;;
esac

# Normalize ID
lc() { printf '%s' "$1" | tr 'A-Z' 'a-z'; }
OS_ID=$(lc "$OS_ID")
OS_LIKE=$(lc "$OS_LIKE")

is_rhel_like=false
is_deb_like=false
case "$OS_ID" in
  almalinux|rocky|rhel|centos|centos_stream|ol|oraclelinux|fedora)
    is_rhel_like=true ;;
  debian|ubuntu)
    is_deb_like=true ;;
  *)
    # Try ID_LIKE
    case "$OS_LIKE" in
      *rhel*|*fedora*) is_rhel_like=true ;;
      *debian*) is_deb_like=true ;;
    esac ;;
esac

# 3) Validate supported versions (coarse)
if $is_rhel_like; then
  # Accept EL8/EL9
  major=$(printf '%s' "$OS_VER" | awk -F. '{print $1}')
  if [ -n "$major" ] && [ "$major" -ge 8 ] && [ "$major" -le 9 ]; then
    :
  else
    warn "Detected $NAME ($OS_ID $OS_VER); DirectAdmin typically supports EL8/EL9. Proceed with caution."
  fi
elif $is_deb_like; then
  # Accept Debian 10/11/12; Ubuntu 20.04/22.04/24.04
  dmajor=$(printf '%s' "$OS_VER" | awk -F. '{print $1}')
  if [ "$OS_ID" = "debian" ]; then
    case "$dmajor" in 10|11|12) : ;; *) warn "Debian $OS_VER not in 10/11/12 – proceed with caution." ;; esac
  elif [ "$OS_ID" = "ubuntu" ]; then
    case "$OS_VER" in 20.04|22.04|24.04) : ;; *) warn "Ubuntu $OS_VER not in 20.04/22.04/24.04 – proceed with caution." ;; esac
  fi
else
  err "Unsupported OS family: ID=$OS_ID (NAME=$NAME). DirectAdmin supports RHEL-like 8/9 and Debian/Ubuntu LTS."
fi

# 4) Package manager + prerequisites
PKG=""
if command -v apt-get >/dev/null 2>&1; then PKG=apt; fi
if command -v dnf >/dev/null 2>&1; then PKG=dnf; fi
if [ -z "$PKG" ] && command -v yum >/dev/null 2>&1; then PKG=yum; fi

need_install=""
for bin in curl wget tar perl; do
  command -v "$bin" >/dev/null 2>&1 || need_install="$need_install $bin"
done
# dig utility: dnsutils (Debian) / bind-utils (RHEL)
if ! command -v dig >/dev/null 2>&1; then
  if $is_deb_like; then need_install="$need_install dnsutils"; else need_install="$need_install bind-utils"; fi
fi

if [ -n "$need_install" ]; then
  if [ "$PKG" = "apt" ]; then
    log "Installing prerequisites via apt-get: $need_install"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y || true
    # shellcheck disable=SC2086
    apt-get install -y $need_install || err "Failed to install some prerequisites: $need_install"
  elif [ "$PKG" = "dnf" ] || [ "$PKG" = "yum" ]; then
    log "Installing prerequisites via $PKG: $need_install"
    # shellcheck disable=SC2086
    $PKG -y install $need_install || err "Failed to install some prerequisites: $need_install"
  else
    warn "Unknown package manager; please install:$need_install"
  fi
fi

# 5) FQDN hostname
hn=$( (hostname -f 2>/dev/null || hostname) | tr -d '\r')
case "$hn" in
  *.*) : ;;  
  *) err "Hostname must be a FQDN (current: '$hn'). Set with: hostnamectl set-hostname server.domain.tld" ;;
esac
case "$hn" in localhost*|*.localdomain) err "Hostname must not be 'localhost' or end with .localdomain (current: '$hn')" ;; esac

# 6) Write environment for subsequent steps
out=/root/directadmin.env
{
  echo "DA_OS_ID=$OS_ID"
  echo "DA_OS_VER=$OS_VER"
  echo "DA_ARCH=$arch"
  echo "DA_PKG_MGR=$PKG"
  echo "DA_HOSTNAME=$hn"
} > "$out"
chmod 600 "$out" || true
log "Wrote DirectAdmin environment: $out"

# Finalize
if $ok; then
  log "DirectAdmin requirement check passed."
  exit 0
else
  err "DirectAdmin requirement check failed."
  exit 1
fi


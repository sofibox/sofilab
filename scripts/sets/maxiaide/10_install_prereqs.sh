#!/usr/bin/env sh
set -eu

echo "Installing prerequisites for AIDE (curl nano diffutils aide/mail)…"

PKG=""
if command -v apt-get >/dev/null 2>&1; then PKG=apt; fi
if command -v dnf >/dev/null 2>&1; then PKG=dnf; fi
if [ -z "$PKG" ] && command -v yum >/dev/null 2>&1; then PKG=yum; fi

if [ "$PKG" = "apt" ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y || true
  apt-get install -y curl nano diffutils aide aide-common mailutils || exit 1
elif [ "$PKG" = "dnf" ] || [ "$PKG" = "yum" ]; then
  # mailx provider varies by distro; this installs a mail client for reports
  $PKG -y install curl nano diffutils aide mailx || exit 1
else
  echo "WARN: Unknown package manager; please ensure curl nano diffutils aide are installed." >&2
fi

echo "OK: Prerequisites installation step completed"
exit 0


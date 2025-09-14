#!/usr/bin/env bash
# pmx-setup-2fa.sh — Proxmox TOTP setup helper
# - Checks whether a Proxmox user already has TOTP configured
# - If not, creates a new TOTP factor and prints a QR code/secret
# - Works for any user (default: root@pam). Accepts username as arg.
# - Interactive when a TTY is present; safe non-interactive fallback.

set -Eeuo pipefail

require_cmd() {
  command -v "$1" >/dev/null 2>&1
}

SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  if require_cmd sudo; then SUDO="sudo"; else echo "This script requires root privileges (pveum)." >&2; exit 1; fi
fi

# Detect Proxmox CLI
if ! require_cmd pveum; then
  echo "❌ pveum not found. This script must run on a Proxmox VE host." >&2
  exit 1
fi

# Resolve target user
INPUT_USER="${1:-}"
DEFAULT_USER="root@pam"

normalize_user() {
  local u="$1"
  if [[ "$u" == *"@"* ]]; then
    echo "$u"
  else
    # No realm provided; default to pam
    echo "${u}@pam"
  fi
}

TARGET_USER=""
if [[ -n "$INPUT_USER" ]]; then
  TARGET_USER="$(normalize_user "$INPUT_USER")"
elif tty -s; then
  echo "Proxmox user to configure TOTP for (default: $DEFAULT_USER)"
  read -r -p "Enter user (user or user@realm): " ans || true
  ans="${ans:-$DEFAULT_USER}"
  TARGET_USER="$(normalize_user "$ans")"
else
  TARGET_USER="$DEFAULT_USER"
fi

echo "=== Proxmox TOTP Guidance ==="
echo "User: $TARGET_USER"
echo "Date: $(date)"
echo

# Helper: Check if user exists (robust across Proxmox versions)
user_exists=false
if require_cmd pvesh; then
  users_json="$($SUDO pvesh get /access/users 2>/dev/null || true)"
  if echo "$users_json" | grep -Eq '"userid"[[:space:]]*:[[:space:]]*"'"$TARGET_USER"'"'; then
    user_exists=true
  fi
fi

if [[ "$user_exists" != true ]]; then
  list_table="$($SUDO pveum user list 2>/dev/null || true)"
  # Try direct userid as first column
  if echo "$list_table" | awk 'NR>1{print $1}' | grep -Fxq "$TARGET_USER"; then
    user_exists=true
  else
    # Some versions print separate columns: User and Realm
    combined=$(echo "$list_table" | awk 'NR>1{if($2!="") print $1"@"$2}')
    if echo "$combined" | grep -Fxq "$TARGET_USER"; then
      user_exists=true
    fi
  fi
fi
if [[ "$user_exists" != true ]]; then
  # Definitive check via cluster user config file
  if [[ -f /etc/pve/user.cfg ]] && grep -Eiq '^user:[[:space:]]*'"$TARGET_USER"':' /etc/pve/user.cfg; then
    user_exists=true
  fi
fi

if [[ "$user_exists" != true ]]; then
  echo "❌ User not found: $TARGET_USER" >&2
  echo "Hint: create the user first, e.g.: pveum user add user@pam" >&2
  exit 1
fi

# Helper: Determine if TOTP is already configured/enabled
has_totp=false
list_out="$($SUDO pveum user tfa list "$TARGET_USER" 2>/dev/null || true)"
if echo "$list_out" | grep -qi '\btotp\b'; then
  # If table includes an Enabled column, try to ensure it's enabled
  if echo "$list_out" | grep -Eqi '\btotp\b.*\b(1|yes|true)\b'; then
    has_totp=true
  else
    # Might not print enabled state; assume presence means configured
    has_totp=true
  fi
else
  # Fallback: inspect user.cfg for totp entries
  if [[ -f /etc/pve/user.cfg ]]; then
    if grep -Eiq "^tfa:\s*.*${TARGET_USER//\//\/}.*totp" /etc/pve/user.cfg; then
      has_totp=true
    fi
  fi
fi

if [[ "$has_totp" == true ]]; then
  echo "✅ TOTP already configured for $TARGET_USER"
  echo "Current factors:"
  $SUDO pveum user tfa list "$TARGET_USER" || true
  exit 0
fi

echo "ℹ️  No TOTP found for $TARGET_USER"
echo
echo "This script will guide you to add TOTP via the Proxmox GUI and then verify it."
if tty -s; then
  echo "Guided steps:"
  echo "  1) Open Proxmox GUI"
  echo "  2) Datacenter → Permissions → Two Factor"
  echo "  3) Click 'Add' → 'TOTP'"
  echo "  4) Select user '$TARGET_USER' and complete the dialog"
  echo "  5) Scan the QR with your authenticator (or copy the secret)"
  echo
  read -r -p "Press Enter after you added TOTP for $TARGET_USER ..." _ || true
  echo "Checking for TOTP (up to 5 minutes)..."
  deadline=$(( $(date +%s) + 300 ))
  created=false
  while (( $(date +%s) < deadline )); do
    if $SUDO pveum user tfa list "$TARGET_USER" 2>/dev/null | grep -qi '\btotp\b'; then
      created=true
      break
    fi
    if [[ -f /etc/pve/user.cfg ]] && grep -Eiq "^tfa:\s*.*${TARGET_USER//\//\/}.*totp" /etc/pve/user.cfg; then
      created=true
      break
    fi
    sleep 5
  done
  if [[ "$created" != true ]]; then
    echo "❌ Did not detect TOTP for $TARGET_USER within timeout." >&2
    echo "    Please ensure it was added correctly and try again." >&2
    exit 1
  fi
else
  echo "Run with --tty for a guided flow, or add TOTP in GUI and rerun to verify." >&2
  exit 1
fi

echo
echo "✅ TOTP detected for $TARGET_USER"
echo "Current factors:"
$SUDO pveum user tfa list "$TARGET_USER" || true
echo
echo "Done. You can manage factors with: pveum user tfa list '$TARGET_USER'"

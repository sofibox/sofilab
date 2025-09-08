#!/bin/bash
# Proxmox Security Setup - FIXED VERSION (no duplicate firewall rules)
# - Properly handles Proxmox's symlinked authorized_keys
# - Sets up SSH key auth correctly for Proxmox
# - Hardens sshd
# - Applies small sysctl hardening
# - Configures Proxmox firewall rules (idempotent; de-duplicates)
# - Readable function names, functions at top

set -euo pipefail

# ========================
# Functions (top section)
# ========================

log() { printf '%s\n' "$*"; }

require_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "This script must be run as root." >&2
    exit 1
  fi
}

resolve_user_home() {
  local user="$1"
  local home
  home="$(getent passwd "$user" | cut -d: -f6)"
  [[ -z "${home:-}" ]] && home="/root"
  printf '%s' "$home"
}

ensure_dir_mode() {
  local path="$1" mode="$2" owner="$3" group="$4"
  mkdir -p "$path"
  chmod "$mode" "$path"
  chown "$owner:$group" "$path"
}

add_pubkey_to_proxmox() {
  local pubfile="$1"
  [[ ! -f "$pubfile" ]] && return 1
  local proxmox_keys="/etc/pve/priv/authorized_keys"
  if [[ -f "$proxmox_keys" ]]; then
    log "Detected Proxmox system - using cluster authorized_keys"
    touch "$proxmox_keys"
    chmod 600 "$proxmox_keys"
    if ! grep -Fqx "$(cat "$pubfile")" "$proxmox_keys" 2>/dev/null; then
      cat "$pubfile" >> "$proxmox_keys"
      log "✓ SSH key added to Proxmox cluster authorized_keys"
      return 0
    else
      log "✓ SSH key already present in Proxmox cluster authorized_keys"
      return 2
    fi
  else
    log "Standard system detected - using regular authorized_keys"
    local auth_keys="/root/.ssh/authorized_keys"
    touch "$auth_keys"
    chmod 600 "$auth_keys"
    if ! grep -Fqx "$(cat "$pubfile")" "$auth_keys" 2>/dev/null; then
      cat "$pubfile" >> "$auth_keys"
      return 0
    fi
    return 2
  fi
}

backup_sshd_config_once_per_day() {
  local today
  today="$(date +%Y%m%d)"
  if ! ls /etc/ssh/sshd_config.backup."$today"_* >/dev/null 2>&1; then
    cp /etc/ssh/sshd_config "/etc/ssh/sshd_config.backup.${today}_$(date +%H%M%S)"
  fi
}

# --- NEW: safer, comment-preserving updater (no awk needed) ---
set_sshd_option_safe() {
  # $1=Key  $2=Value  [$3=path]
  local key="$1" val="$2" file="${3:-/etc/ssh/sshd_config}"

  # If exact active entry exists, nothing to do
  if grep -Eq "^[[:space:]]*${key}[[:space:]]+${val}([[:space:]]|$)" "$file"; then
    log "✓ ${key} already set to ${val}"
    return 0
  fi

  # If an active (uncommented) entry exists with a different value → replace it
  if grep -Eq "^[[:space:]]*${key}[[:space:]]+" "$file"; then
    sed -i -E "s|^[[:space:]]*${key}[[:space:]]+.*|${key} ${val}|" "$file"
    log "✓ ${key} updated to ${val}"
    return 0
  fi

  # If only commented entries exist → append a clean line (keep comments)
  if grep -Eq "^[[:space:]]*#.*${key}" "$file"; then
    echo "${key} ${val}" >> "$file"
    log "✓ ${key} added (new line, comment preserved)"
    return 0
  fi

  # No entry at all → append
  echo "${key} ${val}" >> "$file"
  log "✓ ${key} added"
}

apply_sysctl_hardening() {
  local conf="/etc/sysctl.d/99-security.conf"
  if [[ ! -f "$conf" ]] || ! grep -q "net.ipv4.conf.all.rp_filter" "$conf" 2>/dev/null; then
    cat >"$conf" <<'EOF'
# IP Spoofing protection
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
# Ignore ICMP redirects
net.ipv4.conf.all.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
# Ignore send redirects
net.ipv4.conf.all.send_redirects = 0
# Disable source packet routing
net.ipv4.conf.all.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
# Log martians
net.ipv4.conf.all.log_martians = 1
EOF
    sysctl -p "$conf" >/dev/null 2>&1 || true
    log "✓ Kernel security parameters applied"
  else
    log "✓ Kernel security parameters already configured"
  fi
}

# ---------- FIREWALL (simple, using native Proxmox tools) ----------

fw_enable_datacenter() {
  # Enable datacenter firewall in configuration file first
  local fw_file="/etc/pve/firewall/cluster.fw"
  
  # Ensure the config file exists with proper structure
  if [[ ! -f "$fw_file" ]]; then
    cat > "$fw_file" <<EOF
[OPTIONS]
enable: 1

[RULES]
EOF
    log "✓ Created datacenter firewall configuration with enable: 1"
  else
    # Ensure enable: 1 is set in existing config
    if grep -q "^enable: 0" "$fw_file" 2>/dev/null; then
      sed -i 's/^enable: 0/enable: 1/' "$fw_file"
      log "✓ Changed firewall enable from 0 to 1 in configuration"
    elif ! grep -q "^enable:" "$fw_file" 2>/dev/null; then
      # Add enable option if missing
      if grep -q "^\[OPTIONS\]" "$fw_file"; then
        sed -i "/^\[OPTIONS\]/a enable: 1" "$fw_file"
      else
        sed -i "1i [OPTIONS]\nenable: 1\n" "$fw_file"
      fi
      log "✓ Added enable: 1 to firewall configuration"
    else
      log "✓ Firewall already enabled in configuration (enable: 1)"
    fi
  fi
  
  # Now start the firewall service if needed
  if ! pve-firewall status 2>/dev/null | grep -q "Status: enabled"; then
    pve-firewall start >/dev/null 2>&1 || true
    log "✓ Started Proxmox datacenter firewall service"
  else
    log "✓ Proxmox datacenter firewall service already running"
  fi
}

fw_ensure_rule_exists() {
  local dport="$1" comment="$2" action="${3:-ACCEPT}"
  local fw_file="/etc/pve/firewall/cluster.fw"
  
  # Create firewall config if it doesn't exist
  if [[ ! -f "$fw_file" ]]; then
    cat > "$fw_file" <<EOF
[OPTIONS]
enable: 1

[RULES]
EOF
    log "Created new datacenter firewall configuration"
  fi
  
  # Ensure firewall is enabled in the config
  if grep -q "^enable: 0" "$fw_file" 2>/dev/null; then
    sed -i 's/^enable: 0/enable: 1/' "$fw_file"
    log "✓ Enabled firewall in configuration"
  elif ! grep -q "^enable:" "$fw_file" 2>/dev/null; then
    # Add enable option if missing
    if grep -q "^\[OPTIONS\]" "$fw_file"; then
      sed -i "/^\[OPTIONS\]/a enable: 1" "$fw_file"
    else
      # Add OPTIONS section with enable
      sed -i "1i [OPTIONS]\nenable: 1\n" "$fw_file"
    fi
    log "✓ Added enable option to firewall configuration"
  fi
  
  # Check if rule already exists (any action for this port)
  if grep -q "^IN.*dport ${dport}" "$fw_file" 2>/dev/null; then
    log "✓ Firewall rule exists for port ${dport}"
    return 0
  fi
  
  # Add the rule to the [RULES] section with proper Proxmox format
  # Proxmox firewall uses: IN ACCEPT -p tcp -dport 8006 # comment
  local rule_line="IN ${action} -p tcp -dport ${dport} # ${comment}"
  
  if grep -q "^\[RULES\]" "$fw_file"; then
    # Insert after [RULES] line
    sed -i "/^\[RULES\]/a ${rule_line}" "$fw_file"
  else
    # Add [RULES] section and the rule
    echo "" >> "$fw_file"
    echo "[RULES]" >> "$fw_file"
    echo "${rule_line}" >> "$fw_file"
  fi
  
  log "✓ Added firewall ${action} rule for port ${dport} (${comment})"
}

fw_remove_port_rules() {
  local dport="$1"
  local fw_file="/etc/pve/firewall/cluster.fw"
  local removed=0
  
  if [[ -f "$fw_file" ]]; then
    # Count existing rules for this port (handle grep exit code properly)
    local count=0
    if grep -q "dport ${dport}" "$fw_file" 2>/dev/null; then
      count=$(grep -c "dport ${dport}" "$fw_file" 2>/dev/null)
    fi
    
    if [[ "$count" -gt 0 ]]; then
      # Remove all rules for this port
      sed -i "/dport ${dport}/d" "$fw_file"
      removed="$count"
      log "✓ Removed ${removed} firewall rule(s) for port ${dport}"
    fi
  fi
  
  echo "$removed"
}

fw_cleanup_duplicates() {
  local fw_file="/etc/pve/firewall/cluster.fw"
  
  if [[ ! -f "$fw_file" ]]; then
    return 0
  fi
  
  log "Cleaning up duplicate and malformed firewall rules..."
  
  # Create a temporary file to store unique rules
  local temp_file=$(mktemp)
  local in_rules_section=false
  local seen_ports=()
  
  while IFS= read -r line; do
    if [[ "$line" =~ ^\[RULES\] ]]; then
      echo "$line" >> "$temp_file"
      in_rules_section=true
      continue
    elif [[ "$line" =~ ^\[.*\] ]]; then
      in_rules_section=false
      echo "$line" >> "$temp_file"
      continue
    fi
    
    if [[ "$in_rules_section" == true ]] && [[ "$line" =~ dport[[:space:]]+([0-9]+) ]]; then
      local port="${BASH_REMATCH[1]}"
      
      # Check if we've already seen this port
      local already_seen=false
      for seen_port in "${seen_ports[@]}"; do
        if [[ "$seen_port" == "$port" ]]; then
          already_seen=true
          break
        fi
      done
      
      if [[ "$already_seen" == false ]]; then
        # Clean up malformed rules - remove sport and log parameters if present
        local cleaned_line="$line"
        
        # Remove -sport parameter if it exists
        if [[ "$cleaned_line" =~ -sport[[:space:]]+[0-9]+ ]]; then
          cleaned_line=$(echo "$cleaned_line" | sed -E 's/-sport[[:space:]]+[0-9]+//g')
          log "  ✓ Removed incorrect -sport parameter from port $port rule"
        fi
        
        # Remove -log parameter if it exists
        if [[ "$cleaned_line" =~ -log[[:space:]]+[a-z]+ ]]; then
          cleaned_line=$(echo "$cleaned_line" | sed -E 's/-log[[:space:]]+[a-z]+//g')
          log "  ✓ Removed incorrect -log parameter from port $port rule"
        fi
        
        # Clean up extra spaces
        cleaned_line=$(echo "$cleaned_line" | sed 's/  */ /g' | sed 's/[[:space:]]*$//')
        
        echo "$cleaned_line" >> "$temp_file"
        seen_ports+=("$port")
      else
        log "  ✓ Removed duplicate rule for port $port"
      fi
    else
      echo "$line" >> "$temp_file"
    fi
  done < "$fw_file"
  
  # Replace the original file with the cleaned version (handle Proxmox permissions)
  cat "$temp_file" > "$fw_file" 2>/dev/null || {
    cp "$temp_file" "$fw_file.tmp"
    mv "$fw_file.tmp" "$fw_file"
  }
  rm -f "$temp_file"
}

fw_restart_firewall() {
  # Restart firewall to apply changes
  pve-firewall restart >/dev/null 2>&1 || true
  log "✓ Firewall rules reloaded"
}

test_and_restart_sshd_or_restore() {
  if sshd -t 2>/dev/null; then
    log "✓ SSH configuration is valid"
    if systemctl restart sshd; then
      log "✓ SSH service restarted successfully"
    else
      log "⚠ SSH service restart failed, but configuration is valid"
    fi
  else
    echo "ERROR: sshd configuration invalid! Restoring last backup..." >&2
    if ls /etc/ssh/sshd_config.backup.* >/dev/null 2>&1; then
      cp "$(ls -t /etc/ssh/sshd_config.backup.* | head -1)" /etc/ssh/sshd_config
      systemctl restart sshd || true
      log "Restored sshd_config from backup."
    fi
    exit 1
  fi
}

test_ssh_key_auth() {
  local keyfile="$1" port="$2"
  if [[ -n "$keyfile" && -f "$keyfile" ]]; then
    if ssh -i "$keyfile" -p "$port" -o StrictHostKeyChecking=accept-new \
           -o PasswordAuthentication=no -o ConnectTimeout=5 \
           -o BatchMode=yes localhost "echo ok" >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

print_summary() {
  echo
  echo "=== Proxmox Security Configuration Complete ==="
  echo "✓ SSH key authentication configured"
  if [[ "$DISABLE_PASSWORD_AUTH" == "yes" ]]; then
    echo "✓ Password authentication disabled"
  else
    echo "⚠ Password authentication ENABLED (no SSH keys found)"
  fi
  echo "✓ SSH listens on port: $SSH_LISTEN_PORT"
  echo "✓ Basic system hardening applied"
  echo "✓ Proxmox firewall configured:"
  echo "  - SSH $SSH_LISTEN_PORT: ALLOWED"
  echo "  - 8006 (WebUI): ALLOWED"
  if [[ "$SSH_LISTEN_PORT" != "22" ]]; then
    echo "  - 22: no ACCEPT rule; sshd not listening"
  fi
  echo
  echo "Security Summary:"
  echo "- SSH Port       : $SSH_LISTEN_PORT"
  if [[ "$DISABLE_PASSWORD_AUTH" == "yes" ]]; then
    echo "- Authentication : SSH keys only"
  else
    echo "- Authentication : Password"
  fi
  echo "- WebUI          : 8006 (allowed)"
  echo "- Firewall scope : Datacenter"
  echo
  if [[ -f "/etc/pve/priv/authorized_keys" ]]; then
    echo "Proxmox SSH Key Location:"
    echo "- Cluster keys: /etc/pve/priv/authorized_keys"
    echo "- Symlink:     /root/.ssh/authorized_keys"
    echo
  fi
  if [[ "$KEY_WAS_ADDED" == "true" ]]; then
    echo "Test SSH:"
    echo "  ssh -i \"$SSH_PUBLIC_KEY_PATH_NO_EXT\" -p \"$SSH_LISTEN_PORT\" \"$ADMIN_USER\"@$(hostname -I | awk '{print $1}')"
  else
    echo "Add your key:"
    echo "  ssh-copy-id -i /path/to/key.pub -p \"$SSH_LISTEN_PORT\" \"$ADMIN_USER\"@$(hostname -I | awk '{print $1}')"
  fi
}

# ========================
# Config / Inputs
# ========================

require_root
SSH_LISTEN_PORT="${SSH_PORT:-896}"
CURRENT_CONN_PORT="${ACTUAL_PORT:-$SSH_LISTEN_PORT}"
ADMIN_USER="${ADMIN_USER:-root}"
SSH_PUBLIC_KEY_PATH_NO_EXT="${SSH_KEY_PATH:-}"
SSH_PUBLIC_KEY_CONTENT="${SSH_PUBLIC_KEY:-}"

log "=== Proxmox Security Configuration ==="
log "Configured SSH Port : $SSH_LISTEN_PORT"
log "Current Conn Port   : $CURRENT_CONN_PORT"
log "Target User         : $ADMIN_USER"
log "Date                : $(date)"
echo

# ========================
# PART 1: SSH KEY SETUP
# ========================

log "Step 1: Setting up SSH key authentication..."
ADMIN_HOME="$(resolve_user_home "$ADMIN_USER")"
SSH_DIR="${ADMIN_HOME}/.ssh"
ensure_dir_mode "$SSH_DIR" 700 "$ADMIN_USER" "$ADMIN_USER"

KEY_WAS_ADDED="false"; KEY_AUTH_WORKS="false"

if [[ -f "/etc/pve/priv/authorized_keys" ]]; then
  log "Detected Proxmox VE system"
  [[ -L "$SSH_DIR/authorized_keys" ]] || ln -sf /etc/pve/priv/authorized_keys "$SSH_DIR/authorized_keys"
fi

if [[ -n "$SSH_PUBLIC_KEY_CONTENT" ]]; then
  temp_pubkey="/tmp/sofilab_pubkey_$$.pub"
  echo "$SSH_PUBLIC_KEY_CONTENT" > "$temp_pubkey"
  if add_pubkey_to_proxmox "$temp_pubkey"; then KEY_WAS_ADDED="true"; fi
  rm -f "$temp_pubkey"
  log "✓ SSH key added (via content)"; KEY_AUTH_WORKS="true"
elif [[ -n "$SSH_PUBLIC_KEY_PATH_NO_EXT" ]] && [[ -f "${SSH_PUBLIC_KEY_PATH_NO_EXT}.pub" ]]; then
  if add_pubkey_to_proxmox "${SSH_PUBLIC_KEY_PATH_NO_EXT}.pub"; then KEY_WAS_ADDED="true"; fi
  if test_ssh_key_auth "$SSH_PUBLIC_KEY_PATH_NO_EXT" "$CURRENT_CONN_PORT"; then
    log "✓ SSH key authentication verified"; KEY_AUTH_WORKS="true"
  else
    log "⚠ SSH key added but auth test failed"
  fi
else
  log "No SSH key provided (content/path)."
fi

if [[ -s "/etc/pve/priv/authorized_keys" ]] || [[ -s "$SSH_DIR/authorized_keys" ]]; then
  log "✓ Authorized keys present"
  DISABLE_PASSWORD_AUTH=$([[ "$KEY_AUTH_WORKS" == "true" ]] && echo "yes" || echo "no")
else
  log "WARNING: No SSH keys configured; keeping password auth"
  DISABLE_PASSWORD_AUTH="no"
fi

# ==============================
# PART 2: SSHD CONFIGURATION
# ==============================

echo; log "Step 2: Configuring sshd..."
backup_sshd_config_once_per_day
set_sshd_option_safe "Port" "$SSH_LISTEN_PORT"
set_sshd_option_safe "PubkeyAuthentication" "yes"
set_sshd_option_safe "PermitEmptyPasswords" "no"
set_sshd_option_safe "PermitRootLogin" "prohibit-password"
if [[ "$DISABLE_PASSWORD_AUTH" == "yes" ]]; then
  set_sshd_option_safe "PasswordAuthentication" "no"; log "✓ Password auth disabled (keys verified)"
else
  set_sshd_option_safe "PasswordAuthentication" "yes"; log "⚠ Password auth kept (no verified keys)"
fi

# ==============================
# PART 3: BASIC SYSCTL HARDEN
# ==============================

echo; log "Step 3: Applying basic system hardening..."; apply_sysctl_hardening

# =======================================
# PART 4: PROXMOX FIREWALL CONFIGURATION
# =======================================

echo; log "Step 4: Configuring Proxmox firewall (Datacenter scope)..."
fw_enable_datacenter
fw_cleanup_duplicates
fw_ensure_rule_exists "8006" "Proxmox-WebUI"
fw_ensure_rule_exists "$SSH_LISTEN_PORT" "SSH-Custom-Port"

if [[ "$SSH_LISTEN_PORT" != "22" ]]; then
  log "Removing any rules for port 22 (since SSH moved to $SSH_LISTEN_PORT)..."
  REMOVED_COUNT="$(fw_remove_port_rules 22)"
  if [[ "${REMOVED_COUNT}" -gt 0 ]]; then
    log "✓ Removed ${REMOVED_COUNT} firewall rule(s) for port 22"
  else
    log "✓ No firewall rules found for port 22"
  fi
fi

fw_restart_firewall

echo; log "Firewall note:"
log "- DC firewall enabled; we allow 8006 and ${SSH_LISTEN_PORT}."
log "- sshd not listening on 22; set DC 'Input Policy' to DROP only after confirming rules."

# ==============================
# PART 5: FINALIZATION
# ==============================

echo; log "Step 5: Testing and restarting sshd..."; test_and_restart_sshd_or_restore
print_summary
exit 0
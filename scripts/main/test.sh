#!/bin/bash
# Simplified Proxmox Security Setup Script
# Uses consistent variable names from sofilab

set -e
set -u

# Direct environment variables from sofilab (no renaming)
PORT="${PORT:-896}"              # From sofilab.conf port="896"
USER="${USER:-root}"             # From sofilab.conf user="root"
HOST="${HOST:-}"                 # From sofilab.conf host="..."
KEYFILE="${KEYFILE:-}"          # From sofilab.conf keyfile="..."

echo "=== Proxmox Security Configuration ==="
echo "SSH Port: $PORT"
echo "User: $USER"
echo "Date: $(date)"
echo ""

# Rest of script uses $PORT instead of $SSH_PORT
# Uses $USER instead of $ADMIN_USER
# etc...
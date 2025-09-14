#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

echo "===== testarg1.sh ====="
echo "Args count: $#"
idx=0
for a in "$@"; do printf 'Arg[%02d]: %q\n' "$idx" "$a"; idx=$((idx+1)); done
name=""
if [[ ${1-} == "--name" && ${2-} ]]; then name="$2"; fi
echo "Name: ${name:-<none>}"
echo "SSH_PORT=${SSH_PORT:-}, ADMIN_USER=${ADMIN_USER:-}"
echo "Done testarg1"

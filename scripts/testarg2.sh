#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

echo "===== testarg2.sh ====="
echo "Args count: $#"
idx=0
for a in "$@"; do printf 'Arg[%02d]: %q\n' "$idx" "$a"; idx=$((idx+1)); done
echo "First arg (if any): ${1-<none>}"
echo "Working dir: $(pwd)"
echo "Done testarg2"

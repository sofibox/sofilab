#!/usr/bin/env bash
# Simple script to demonstrate receiving arguments and env from sofilab

set -o nounset
set -o pipefail

echo "===== args-demo.sh ====="
echo "Script: $0"
echo "Argc: $#"
idx=0
for a in "$@"; do
  printf 'Arg[%02d]: %q\n' "$idx" "$a"
  idx=$((idx+1))
done

echo ""
echo "Environment (from SofiLab):"
echo "  SSH_PORT     = ${SSH_PORT:-}"
echo "  ACTUAL_PORT  = ${ACTUAL_PORT:-}"
echo "  ADMIN_USER   = ${ADMIN_USER:-}"
echo "  SSH_KEY_PATH = ${SSH_KEY_PATH:-}"
if [[ -n "${SSH_PUBLIC_KEY:-}" ]]; then
  echo "  SSH_PUBLIC_KEY = <present>"
else
  echo "  SSH_PUBLIC_KEY = <empty>"
fi

echo ""
echo "Working directory: $(pwd)"
echo "Date: $(date)"

# Optional behavior toggles via args for testing
case "${1-}" in
  fail|exit1)
    echo "Simulating failure (exit 1)"
    exit 1
    ;;
esac

echo "All good."
exit 0


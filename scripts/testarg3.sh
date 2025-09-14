#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

echo "===== testarg3.sh ====="
echo "Args count: $#"
idx=0
for a in "$@"; do printf 'Arg[%02d]: %q\n' "$idx" "$a"; idx=$((idx+1)); done

FLAG=""
PATH_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --flag) FLAG="yes"; shift ;;
    --path) PATH_ARG="${2-}"; shift 2 ;;
    *) shift ;;
  esac
done
echo "FLAG=${FLAG:-no}"
echo "PATH_ARG=${PATH_ARG:-<none>}"
echo "Done testarg3"

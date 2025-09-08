#!/usr/bin/env bash
# tty-demo.sh — Demonstrates TTY vs non-TTY behavior
#
# Behavior:
# - If running with a TTY, prompts interactively for a value.
# - If running without a TTY, it avoids hanging by:
#   - Using --color ARG or FAVORITE_COLOR env var if provided
#   - Otherwise reading one line from stdin if piped (with short timeout)
#   - Falling back to a default value if nothing provided

set -Eeuo pipefail

# Parse optional argument: --color <value>
COLOR_FROM_ARG=""
if [[ "${1:-}" == "--color" && -n "${2:-}" ]]; then
  COLOR_FROM_ARG="$2"
fi

# Prefer explicit arg, then env, otherwise empty (decide later)
favorite_color="${COLOR_FROM_ARG:-${FAVORITE_COLOR:-}}"

echo "=== TTY Demo Script ==="
echo "stdin_tty:  $([ -t 0 ] && echo yes || echo no)"
echo "stdout_tty: $([ -t 1 ] && echo yes || echo no)"
echo "stderr_tty: $([ -t 2 ] && echo yes || echo no)"
if tty -s; then echo "has_controlling_tty: yes"; else echo "has_controlling_tty: no"; fi
echo "date: $(date)"
echo

if tty -s; then
  # Interactive path: prompt only when a TTY is present
  if [[ -z "$favorite_color" ]]; then
    read -r -p "Enter your favorite color: " favorite_color
  else
    echo "Using provided color: $favorite_color"
  fi
else
  # Non-interactive path: don't block waiting for input
  if [[ -z "$favorite_color" ]]; then
    # If input is already available on stdin (piped), read a single line with a short timeout
    if read -r -t 0; then
      # Read (up to) one line from stdin; if the remote stdin isn't a TTY but is open, this succeeds
      read -r -t 5 favorite_color || true
    fi
  fi
  if [[ -z "$favorite_color" ]]; then
    favorite_color="blue"
    echo "Non-TTY detected; no input provided. Defaulting to: $favorite_color"
  else
    echo "Non-TTY detected; using provided color: $favorite_color"
  fi
fi

echo
echo "Result => favorite_color: $favorite_color"
if tty -s; then
  echo "Mode: interactive (TTY present)"
else
  echo "Mode: non-interactive (no TTY)"
fi
echo "Done."

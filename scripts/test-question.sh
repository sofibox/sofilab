#!/bin/bash
# Simple Remote Question Script
# Tests remote server input/output capability
# Compatible with sofilab environment variables

set -e
set -u

# Environment variables from sofilab (no renaming needed)
PORT="${PORT:-22}"               # From sofilab.conf port (default 22 if not set)
USER="${USER:-root}"             # From sofilab.conf user
HOST="${HOST:-}"                 # From sofilab.conf host
KEYFILE="${KEYFILE:-}"          # From sofilab.conf keyfile

echo "=== Remote Server Question Test ==="
echo "Connected to: $USER@$HOST:$PORT"
echo "Date: $(date)"
echo ""

# Simple question to test input/output
echo "🤖 Hello! This is a simple test to verify remote server interaction."
echo ""
echo "💭 What is your favorite color?"
echo -n "   Please type your answer: "
read -r favorite_color

echo ""
echo "✨ Great choice! You said: '$favorite_color'"
echo ""

# Test some basic system info to show output capability
echo "📊 Here's some basic info about this server:"
echo "   - Hostname: $(hostname)"
echo "   - Current user: $(whoami)"
echo "   - Current directory: $(pwd)"
echo "   - System uptime: $(uptime | sed 's/.*up //' | sed 's/, [0-9]* user.*//')"
echo ""

echo "🎉 Test completed successfully!"
echo "✅ Remote server can accept input and display output properly."
echo ""
echo "Goodbye! 👋"

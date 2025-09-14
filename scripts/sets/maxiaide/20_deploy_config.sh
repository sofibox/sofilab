#!/usr/bin/env sh
set -eu

RULE_DIR="/etc/aide/aide.conf.d"
RULE_FILE="$RULE_DIR/98_maxiaide_rules"

mkdir -p "$RULE_DIR"

cat > "$RULE_FILE" <<'RULEEOF'
# Maxiaide custom AIDE rules (minimal example)
# Example: flag bash history files as immutable (f) or adjust as needed
!/root/\.bash_history$ f
# Add additional patterns below; see `man aide.conf` for rule syntax.
RULEEOF

chmod 644 "$RULE_FILE"
echo "OK: Deployed AIDE custom rules at $RULE_FILE"
exit 0

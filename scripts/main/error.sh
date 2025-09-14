#!/bin/bash
# Test for error for script execution failure

echo "[REMOTE] Starting error test script..."

# Run syntax error command - 'ech' is intentionally misspelled to cause an error
ech "[REMOTE] This line should not appear because 'ech' is not a valid command"
echo "[REMOTE] This line should not appear because the script already exit from the previous line with error 127"
exit 1
echo "[REMOTE] This line should not appear because the script already exit manually with error 1"

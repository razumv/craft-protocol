#!/bin/zsh
# SPDX-License-Identifier: Apache-2.0
# Deterministic worker watchdog: no prompts, no model calls, no session creation.
set -u
LOG="$HOME/.craft-agent/logs/worker-watchdog.log"
PY=/opt/homebrew/bin/python3
SCRIPT="$HOME/.craft-agent/scripts/worker-watchdog.py"
mkdir -p "$HOME/.craft-agent/logs" "$HOME/.craft-agent/runtime/worker-leases" "$HOME/.craft-agent/runtime/worker-jobs"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') watchdog run =====" >> "$LOG"
"$PY" "$SCRIPT" --apply >> "$LOG" 2>&1
echo "" >> "$LOG"

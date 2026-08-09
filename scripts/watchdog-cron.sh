#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Deterministic worker watchdog: no prompts, no model calls, no session creation.
set -u
LOG="$HOME/.craft-agent/logs/worker-watchdog.log"
PY="${CRAFT_PYTHON:-}"
SCRIPT="$HOME/.craft-agent/scripts/worker-watchdog.py"
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  PY=$(command -v python3 2>/dev/null || true)
fi
if [ -z "$PY" ]; then
  echo "python3 not found; watchdog skipped" >&2
  exit 1
fi
mkdir -p "$HOME/.craft-agent/logs" "$HOME/.craft-agent/runtime/worker-leases" "$HOME/.craft-agent/runtime/worker-jobs"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') watchdog run =====" >> "$LOG"
"$PY" "$SCRIPT" --apply >> "$LOG" 2>&1
echo "" >> "$LOG"

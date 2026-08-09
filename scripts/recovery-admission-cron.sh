#!/bin/zsh
# SPDX-License-Identifier: Apache-2.0
# launchd entrypoint: report-only under kill switch, admitted tick otherwise.
set -eu

CRAFT_HOME="${CRAFT_HOME:-$HOME/.craft-agent}"
PYTHON="${CRAFT_PYTHON:-/opt/homebrew/bin/python3}"
[[ -x "$PYTHON" ]] || PYTHON=$(command -v python3)
SCRIPT="$CRAFT_HOME/scripts/recovery-admission.py"
RUNTIME="$CRAFT_HOME/runtime"
CONFIG="$RUNTIME/self-healing/persistent-controller.json"
LOG="$CRAFT_HOME/logs/recovery-admission.log"
mkdir -p "${LOG:h}" "$RUNTIME/self-healing"

if [[ -e "$RUNTIME/self-healing.disabled" ]]; then
  "$PYTHON" "$SCRIPT" disarm --apply >> "$LOG" 2>&1
  "$PYTHON" "$SCRIPT" report >> "$LOG" 2>&1
  exit $?
fi

if [[ ! -r "$CONFIG" ]]; then
  print -r -- '{"error":"persistent controller config missing; refusing admission"}' >> "$LOG"
  exit 2
fi
CONTROLLER=$(
  "$PYTHON" -c 'import json,sys; value=json.load(open(sys.argv[1])); sid=value.get("sessionId"); assert isinstance(sid,str) and sid; print(sid)' "$CONFIG"
) || {
  print -r -- '{"error":"persistent controller config invalid; refusing admission"}' >> "$LOG"
  exit 2
}
"$PYTHON" "$SCRIPT" tick --controller-session "$CONTROLLER" --apply >> "$LOG" 2>&1

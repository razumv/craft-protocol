#!/bin/zsh
# SPDX-License-Identifier: Apache-2.0
# launchd entrypoint: report-only under kill switch, direct delivery only when explicitly configured.
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
read -r CONTROLLER WORKSPACE_ID EXPECTED_RUNTIME_VERSION EXPECTED_RUNTIME_COMMIT SERVER_URL < <(
  "$PYTHON" -c 'import json,sys
v=json.load(open(sys.argv[1]))
fields=[v.get("sessionId"),v.get("workspaceId"),v.get("expectedRuntimeVersion"),v.get("expectedRuntimeCommit"),v.get("serverUrl","")]
assert all(isinstance(x,str) and x and not any(c in x for c in "\r\n\t") for x in fields[:4])
assert isinstance(fields[4],str) and not any(c in fields[4] for c in "\r\n\t")
print("\t".join(fields))' "$CONFIG"
) || {
  print -r -- '{"error":"persistent controller direct-delivery config invalid; refusing admission"}' >> "$LOG"
  exit 2
}
if [[ -n "$SERVER_URL" ]]; then
  export CRAFT_SERVER_URL="$SERVER_URL"
fi
"$PYTHON" "$SCRIPT" tick --controller-session "$CONTROLLER" --workspace-id "$WORKSPACE_ID" \
  --expected-runtime-version "$EXPECTED_RUNTIME_VERSION" --expected-runtime-commit "$EXPECTED_RUNTIME_COMMIT" --apply >> "$LOG" 2>&1

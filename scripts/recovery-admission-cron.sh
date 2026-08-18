#!/bin/zsh
# SPDX-License-Identifier: Apache-2.0
# launchd entrypoint: report-only under kill switch; capability-v2 target cycles only when explicitly configured.
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
read -r CONTROLLER WORKSPACE_ID EXPECTED_RUNTIME_VERSION EXPECTED_RUNTIME_COMMIT SERVER_URL RPC_CLI CLI_TIMEOUT SUPERVISOR_TIMEOUT < <(
  "$PYTHON" -c 'import json,sys
from urllib.parse import urlsplit
v=json.load(open(sys.argv[1]))
fields=[v.get("sessionId"),v.get("workspaceId"),v.get("expectedRuntimeVersion"),v.get("expectedRuntimeCommit"),v.get("serverUrl"),v.get("rpcCli")]
assert all(isinstance(x,str) and x and not any(c in x for c in "\r\n\t") for x in fields)
url=urlsplit(fields[4]); assert url.scheme=="wss" or (url.scheme=="ws" and url.hostname in {"127.0.0.1","::1","localhost"})
assert fields[5].startswith("/")
cli=v.get("cliTimeoutSeconds",110); supervisor=v.get("supervisorTimeoutSeconds",120)
assert isinstance(cli,int) and not isinstance(cli,bool) and 20 <= cli <= 300
assert isinstance(supervisor,int) and not isinstance(supervisor,bool) and 30 <= supervisor <= 330
assert supervisor >= cli + 5
print("\t".join([*fields,str(cli),str(supervisor)]))' "$CONFIG"
) || {
  print -r -- '{"error":"persistent controller direct-delivery config invalid; refusing admission"}' >> "$LOG"
  exit 2
}
if [[ ! -x "$RPC_CLI" ]]; then
  print -r -- '{"error":"configured Craft RPC CLI is not executable; refusing admission"}' >> "$LOG"
  exit 2
fi
export CRAFT_RPC_CLI="$RPC_CLI"
export CRAFT_SERVER_URL="$SERVER_URL"
export CRAFT_ADMISSION_CLI_TIMEOUT_SECONDS="$CLI_TIMEOUT"
export CRAFT_ADMISSION_SUPERVISOR_TIMEOUT_SECONDS="$SUPERVISOR_TIMEOUT"

# Detection is deterministic and needs no agent, so it must not depend on one.
# Until v3.4.25 only the controller session ran `detect --apply`; when that lane
# went quiet the ledger froze too, and admission kept deciding from a stale view
# while conditions piled up unseen. Perception now happens every tick regardless
# of whether any controller turn ever starts.
"$PYTHON" "${SCRIPT:h}/recovery-incident.py" detect --apply >> "$LOG" 2>&1 || \
  print -r -- '{"warning":"incident detection failed this tick"}' >> "$LOG"

"$PYTHON" "$SCRIPT" tick --controller-session "$CONTROLLER" --workspace-id "$WORKSPACE_ID" \
  --expected-runtime-version "$EXPECTED_RUNTIME_VERSION" --expected-runtime-commit "$EXPECTED_RUNTIME_COMMIT" --apply >> "$LOG" 2>&1

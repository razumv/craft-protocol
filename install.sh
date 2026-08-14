#!/bin/zsh
# SPDX-License-Identifier: Apache-2.0
# Safe installer for Craft Agents orchestration protocol v3.4.11.
# Dry-run by default. Use --apply only after reviewing README.md.
set -eu

APPLY=0
WORKSPACE="${CRAFT_WORKSPACE:-$HOME/.craft-agent/workspaces/general}"
while (( $# )); do
  case "$1" in
    --apply) APPLY=1 ;;
    --workspace) shift; WORKSPACE="$1" ;;
    -h|--help) echo "Usage: ./install.sh [--apply] [--workspace PATH]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

ROOT="${0:A:h}"
CRAFT="$HOME/.craft-agent"
SCRIPTS="$CRAFT/scripts"
SKILLS="$WORKSPACE/skills"
RUNTIME="$CRAFT/runtime"
LOGS="$CRAFT/logs"
STAMP=$(date '+%Y%m%d-%H%M%S')
BACKUP="$CRAFT/backups/orchestration-v3.4.11-$STAMP"
PYTHON="${CRAFT_PYTHON:-/opt/homebrew/bin/python3}"
[[ -x "$PYTHON" ]] || PYTHON=$(command -v python3)
PLIST_NAME="com.craft-protocol.worker-watchdog.plist"
ADMISSION_PLIST_NAME="com.craft-protocol.recovery-admission.plist"

files=(
  orchestration-common.py coordinator-registry.py coordinator-reconcile.py
  owner-gate.py recovery-ledger.py completion-certificate.py recovery-incident.py
  worker-lease.py observable-job.py external-wait.py worker-watchdog.py post-archive-reaper.py
  controller-harness.py recovery-admission.py
  coordinator-inbox.py coordinator-status.py coordinator-commitment.py owner-gate-board.py
  scan-reapable-workers.py watchdog-cron.sh recovery-admission-cron.sh coordinator-kickoff.md
)

echo "Mode: $([[ $APPLY == 1 ]] && echo APPLY || echo DRY-RUN)"
echo "Bundle: $ROOT"
echo "Workspace: $WORKSPACE"
echo "Scripts: $SCRIPTS"
echo "Backup: $BACKUP"
echo "Python: $PYTHON"
echo

backup_existing() {
  local dst="$1" rel backup_dst
  [[ -e "$dst" ]] || return 0
  rel="${dst#$HOME/}"
  backup_dst="$BACKUP/$rel"
  mkdir -p "${backup_dst:h}"
  cp -p "$dst" "$backup_dst"
}

install_file() {
  local src="$1" dst="$2"
  echo "INSTALL $src -> $dst"
  if (( APPLY )); then
    mkdir -p "${dst:h}"
    backup_existing "$dst"
    cp -p "$src" "$dst"
  fi
}

# FIRST safety mutation: restore the absolute kill switch before any v3.4.0
# script, skill, config, or launchd payload can be copied. Any later failure
# therefore leaves admission disabled.
if (( APPLY )); then
  mkdir -p "$RUNTIME"
  : > "$RUNTIME/self-healing.disabled"
  chmod 600 "$RUNTIME/self-healing.disabled"
  echo "RESTORED KILL SWITCH $RUNTIME/self-healing.disabled before payload mutation"
fi

for name in $files; do
  install_file "$ROOT/scripts/$name" "$SCRIPTS/$name"
done

install_file "$ROOT/skills/coordinator-lifecycle-protocol/SKILL.md" \
  "$SKILLS/coordinator-lifecycle-protocol/SKILL.md"
install_file "$ROOT/skills/worker-completion-protocol/SKILL.md" \
  "$SKILLS/worker-completion-protocol/SKILL.md"
install_file "$ROOT/skills/self-healing-controller/SKILL.md" \
  "$SKILLS/self-healing-controller/SKILL.md"

PLIST_DST="$SCRIPTS/$PLIST_NAME"
echo "RENDER $ROOT/config/launchd.watchdog.template.plist -> $PLIST_DST"
if (( APPLY )); then
  mkdir -p "$SCRIPTS" "$RUNTIME/worker-leases" "$RUNTIME/worker-jobs" \
    "$RUNTIME/coordinators" "$RUNTIME/owner-gates" \
    "$RUNTIME/recovery-ledger" "$RUNTIME/completion-certificates" \
    "$RUNTIME/recovery-incidents" "$RUNTIME/self-healing" "$LOGS"
  chmod 700 "$RUNTIME" "$LOGS" 2>/dev/null || true
  backup_existing "$PLIST_DST"
  sed "s|__HOME__|$HOME|g; s|__PYTHON__|$PYTHON|g" \
    "$ROOT/config/launchd.watchdog.template.plist" > "$PLIST_DST"
  ADMISSION_PLIST_DST="$SCRIPTS/$ADMISSION_PLIST_NAME"
  backup_existing "$ADMISSION_PLIST_DST"
  sed "s|__HOME__|$HOME|g" "$ROOT/config/launchd.admission.template.plist" > "$ADMISSION_PLIST_DST"
  chmod 700 "$SCRIPTS"/*.py "$SCRIPTS"/*.sh
  chmod 600 "$PLIST_DST" "$ADMISSION_PLIST_DST"
  backup_existing "$WORKSPACE/automations.json"
  "$PYTHON" "$SCRIPTS/recovery-admission.py" install-guard \
    --template "$ROOT/config/self-healing.automations.template.json" --apply
  echo "Capability-v2 recovery and direct coordinator ticks remain disabled until persistent-controller.json explicitly provides sessionId, workspaceId, expectedRuntimeVersion, expectedRuntimeCommit, a trusted serverUrl, and an absolute executable rpcCli."
  echo "Provide CRAFT_SERVER_TOKEN in the service environment or an owner-only token file; no token/version defaults are installed."
fi

echo
echo "LABELS ARE NOT AUTOMATICALLY INSTALLED. Review and merge:"
echo "  $ROOT/config/labels.config.json"
echo "into:"
echo "  $WORKSPACE/labels/config.json"

echo
if (( APPLY )); then
  echo "Verifying package hashes..."
  (cd "$ROOT" && shasum -a 256 -c manifest.sha256)
  echo "Compiling Python scripts..."
  "$PYTHON" -m py_compile "$SCRIPTS"/*.py
  echo "Running regression tests against installed scripts..."
  (cd "$ROOT/tests" && CRAFT_TEST_SCRIPTS="$SCRIPTS" "$PYTHON" -m unittest -v \
    test_worker_reliability.py test_orchestration_v320.py test_self_healing_v311.py \
    test_delivery_mode_v320.py test_controller_harness_v321.py \
    test_recovery_admission_v322.py test_external_wait_v321.py test_coordinator_v330.py)
  echo "Running watchdog dry-run..."
  "$PYTHON" "$SCRIPTS/worker-watchdog.py"
  echo "Install complete. Review output before enabling launchd."
else
  echo "No files changed. Re-run with --apply after review."
fi

echo
echo "MANDATORY RUNTIME-FIRST ACTIVATION GATE:"
echo "  1. Install/start production-tested capability-v2 runtime 87951ae640df64d00534a54dce9b5e8b5922d27c first."
echo "  2. Keep '$RUNTIME/self-healing.disabled' present."
echo "  3. Verify exact identity before launchd activation or kill-switch removal:"
echo "     CRAFT_SERVER_URL=<trusted-url> CRAFT_RPC_CLI=<absolute-cli> $SCRIPTS/recovery-admission.py verify-runtime \\"
echo "       --workspace-id <workspace-id> --expected-runtime-version 0.11.4-admission.87951ae --expected-runtime-commit 87951ae640df64d00534a54dce9b5e8b5922d27c"
echo "  4. Activate report-only launchd only after verified=true; remove the kill switch only after reviewed canary approval."

echo
echo "Optional launchd activation (manual review required after verified=true):"
echo "  mkdir -p ~/Library/LaunchAgents"
echo "  cp '$PLIST_DST' ~/Library/LaunchAgents/$PLIST_NAME"
echo "  launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/$PLIST_NAME"
echo
echo "Optional recovery-admission report-only canary (kill switch must remain present):"
echo "  cp '$SCRIPTS/$ADMISSION_PLIST_NAME' ~/Library/LaunchAgents/$ADMISSION_PLIST_NAME"
echo "  launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/$ADMISSION_PLIST_NAME"

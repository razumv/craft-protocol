#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Safe installer for Craft Agents orchestration protocol v3.2.0.
# Dry-run by default. Use --apply only after reviewing README.md.
set -euo pipefail

APPLY=0
WORKSPACE="${CRAFT_WORKSPACE:-$HOME/.craft-agent/workspaces/general}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --workspace)
      shift
      [ "$#" -gt 0 ] || { echo "--workspace requires a path" >&2; exit 2; }
      WORKSPACE="$1"
      ;;
    -h|--help) echo "Usage: ./install.sh [--apply] [--workspace PATH]"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CRAFT="$HOME/.craft-agent"
SCRIPTS="$CRAFT/scripts"
SKILLS="$WORKSPACE/skills"
RUNTIME="$CRAFT/runtime"
LOGS="$CRAFT/logs"
STAMP=$(date '+%Y%m%d-%H%M%S')
BACKUP="$CRAFT/backups/orchestration-v3.2.0-$STAMP"
PYTHON="${CRAFT_PYTHON:-}"
if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
  PYTHON=$(command -v python3 || true)
fi
[ -n "$PYTHON" ] || { echo "python3 is required" >&2; exit 1; }

files='orchestration-common.py coordinator-registry.py coordinator-reconcile.py
owner-gate.py recovery-ledger.py completion-certificate.py recovery-incident.py
worker-lease.py observable-job.py worker-watchdog.py post-archive-reaper.py
scan-reapable-workers.py watchdog-cron.sh coordinator-kickoff.md'

if [ "$APPLY" -eq 1 ]; then MODE=APPLY; else MODE=DRY-RUN; fi
echo "Mode: $MODE"
echo "Bundle: $ROOT"
echo "Workspace: $WORKSPACE"
echo "Scripts: $SCRIPTS"
echo "Backup: $BACKUP"
echo "Python: $PYTHON"
echo

backup_existing() {
  dst=$1
  [ -e "$dst" ] || return 0
  rel=${dst#"$HOME"/}
  backup_dst="$BACKUP/$rel"
  mkdir -p "$(dirname -- "$backup_dst")"
  cp -p "$dst" "$backup_dst"
}

install_file() {
  src=$1 dst=$2
  echo "INSTALL $src -> $dst"
  if [ "$APPLY" -eq 1 ]; then
    mkdir -p "$(dirname -- "$dst")"
    backup_existing "$dst"
    cp -p "$src" "$dst"
  fi
}

for name in $files; do
  install_file "$ROOT/scripts/$name" "$SCRIPTS/$name"
done

install_file "$ROOT/skills/coordinator-lifecycle-protocol/SKILL.md" \
  "$SKILLS/coordinator-lifecycle-protocol/SKILL.md"
install_file "$ROOT/skills/worker-completion-protocol/SKILL.md" \
  "$SKILLS/worker-completion-protocol/SKILL.md"
install_file "$ROOT/skills/self-healing-controller/SKILL.md" \
  "$SKILLS/self-healing-controller/SKILL.md"

PLIST_NAME="com.craft-protocol.worker-watchdog.plist"
SYSTEMD_SERVICE_NAME="craft-protocol-worker-watchdog.service"
SYSTEMD_TIMER_NAME="craft-protocol-worker-watchdog.timer"
PLIST_DST="$SCRIPTS/$PLIST_NAME"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SYSTEMD_SERVICE_DST="$SYSTEMD_USER_DIR/$SYSTEMD_SERVICE_NAME"
SYSTEMD_TIMER_DST="$SYSTEMD_USER_DIR/$SYSTEMD_TIMER_NAME"
echo "RENDER $ROOT/config/launchd.watchdog.template.plist -> $PLIST_DST"
echo "RENDER $ROOT/config/systemd.worker-watchdog.service.template -> $SYSTEMD_SERVICE_DST"
echo "RENDER $ROOT/config/systemd.worker-watchdog.timer.template -> $SYSTEMD_TIMER_DST"
if [ "$APPLY" -eq 1 ]; then
  mkdir -p "$SCRIPTS" "$RUNTIME/worker-leases" "$RUNTIME/worker-jobs" \
    "$RUNTIME/coordinators" "$RUNTIME/owner-gates" \
    "$RUNTIME/recovery-ledger" "$RUNTIME/completion-certificates" \
    "$RUNTIME/recovery-incidents" "$RUNTIME/self-healing" "$LOGS"
  chmod 700 "$RUNTIME" "$LOGS" 2>/dev/null || true
  backup_existing "$PLIST_DST"
  sed "s|__HOME__|$HOME|g; s|__PYTHON__|$PYTHON|g" \
    "$ROOT/config/launchd.watchdog.template.plist" > "$PLIST_DST"
  mkdir -p "$SYSTEMD_USER_DIR"
  backup_existing "$SYSTEMD_SERVICE_DST"
  backup_existing "$SYSTEMD_TIMER_DST"
  sed "s|__HOME__|$HOME|g; s|__PYTHON__|$PYTHON|g" \
    "$ROOT/config/systemd.worker-watchdog.service.template" > "$SYSTEMD_SERVICE_DST"
  cp -p "$ROOT/config/systemd.worker-watchdog.timer.template" "$SYSTEMD_TIMER_DST"
  chmod 700 "$SCRIPTS"/*.py "$SCRIPTS"/*.sh
  chmod 600 "$PLIST_DST" "$SYSTEMD_SERVICE_DST" "$SYSTEMD_TIMER_DST"
fi

echo
echo "LABELS ARE NOT AUTOMATICALLY INSTALLED. Review and merge:"
echo "  $ROOT/config/labels.config.json"
echo "into:"
echo "  $WORKSPACE/labels/config.json"

echo
if [ "$APPLY" -eq 1 ]; then
  echo "Verifying package hashes..."
  (cd "$ROOT" && ./tools/generate-manifest.sh --check)
  echo "Compiling Python scripts..."
  "$PYTHON" -m py_compile "$SCRIPTS"/*.py
  echo "Running regression tests against installed scripts..."
  (cd "$ROOT/tests" && CRAFT_TEST_SCRIPTS="$SCRIPTS" "$PYTHON" -m unittest -v \
    test_worker_reliability.py test_orchestration_v320.py test_self_healing_v311.py test_delivery_mode_v320.py)
  echo "Running watchdog dry-run..."
  "$PYTHON" "$SCRIPTS/worker-watchdog.py"
  echo "Install complete. Review output before enabling a scheduler."
else
  echo "No files changed. Re-run with --apply after review."
fi

echo
echo "Optional macOS launchd activation (manual review required):"
echo "  mkdir -p ~/Library/LaunchAgents"
echo "  cp '$PLIST_DST' ~/Library/LaunchAgents/$PLIST_NAME"
echo "  launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/$PLIST_NAME"
echo
echo "Optional Linux systemd user timer activation (manual review required):"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now $SYSTEMD_TIMER_NAME"

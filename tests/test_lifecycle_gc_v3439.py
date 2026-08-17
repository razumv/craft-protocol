# SPDX-License-Identifier: Apache-2.0
"""Adversarial v3.4.39 lifecycle-debt and worktree-GC regressions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts")).expanduser().resolve()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"; self.sessions = self.workspace / "sessions"
        self.runtime = self.root / "runtime"; self.sessions.mkdir(parents=True)
        self.now = int(time.time() * 1000)
        self.report = self.root / "worktree-gc.json"
        self.env = {**os.environ, "CRAFT_WORKSPACE": str(self.workspace),
                    "CRAFT_SESSIONS": str(self.sessions), "CRAFT_RUNTIME": str(self.runtime),
                    "CRAFT_TEST_NOW_MS": str(self.now), "CRAFT_GC_CWD_HOLDERS_JSON": "{}",
                    "CRAFT_WORKTREE_GC_REPORT": str(self.report)}

    def tearDown(self): self.temp.cleanup()

    def put(self, path: Path, value: dict):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value) + "\n")

    def manifest(self, sid: str, role: str, cwd: Path | None = None, *, archived=False,
                 status="in_progress", labels=None, **extra):
        value = {"id": sid, "isArchived": archived, "sessionStatus": status,
                 "labels": [f"agent-role::{role}", *(labels or [])], **extra}
        if cwd is not None: value["workingDirectory"] = str(cwd.resolve())
        self.put(self.sessions / sid / "session.jsonl", value)
        return value

    def cli(self, script: str, *args: str, ok=True):
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), *args], env=self.env,
                              text=True, capture_output=True, timeout=90)
        if ok and proc.returncode:
            self.fail(f"{script} failed {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")
        return proc, json.loads(proc.stdout) if proc.stdout else None


class WorktreeGcTests(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.root / "repo"; self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Lifecycle Test")
        (self.repo / "seed").write_text("seed\n"); self.git("add", "seed"); self.git("commit", "-m", "seed")
        self.worktrees = self.repo / ".worktrees"; self.worktrees.mkdir()

    def git(self, *args: str, cwd: Path | None = None, check=True):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, check=check,
                              text=True, capture_output=True)

    def add_branch(self, name: str) -> Path:
        path = self.worktrees / name
        self.git("worktree", "add", "-b", f"lane-{name}", str(path), "main")
        return path

    def add_detached(self, name: str, unreachable=False) -> Path:
        path = self.worktrees / name
        self.git("worktree", "add", "--detach", str(path), "main")
        if unreachable:
            (path / "detached-only").write_text(name)
            self.git("add", "detached-only", cwd=path); self.git("commit", "-m", "detached only", cwd=path)
        return path

    def gc(self, *args: str, ok=True):
        return self.cli("worktree-gc.py", "--repo", str(self.repo), "--output", str(self.report), *args, ok=ok)

    def test_dry_run_protects_every_safety_class_and_writes_durable_json(self):
        active = self.add_branch("active")
        active_alternate = self.add_branch("active-alternate")
        active_child = self.add_branch("active-child")
        shared = self.add_branch("shared")
        held = self.add_branch("held")
        dirty = self.add_branch("dirty"); (dirty / "untracked").write_text("do not lose")
        detached = self.add_detached("detached", unreachable=True)
        recent = self.add_branch("recent")
        eligible = self.add_branch("eligible")

        active_subdir = active / "nested"; active_subdir.mkdir()
        held_subdir = held / "nested"; held_subdir.mkdir()
        self.manifest("active", "worker", active_subdir, sdkCwd=str(active_alternate))
        self.manifest("archived-child", "worker", active_child, archived=True)
        self.manifest("shared-a", "worker", shared, archived=True)
        self.manifest("shared-b", "auditor", shared, archived=True)
        self.manifest("held-owner", "worker", held, archived=True)
        self.manifest("dirty-owner", "worker", dirty, archived=True)
        self.manifest("detached-owner", "worker", detached, archived=True)
        self.manifest("eligible-owner", "worker", eligible, archived=True)
        self.put(self.runtime / "coordinators" / "demo.json", {
            "project": "demo", "state": "authoritative", "coordinatorSessionId": "coord",
            "activeChildren": ["archived-child"]})
        self.env["CRAFT_GC_CWD_HOLDERS_JSON"] = json.dumps({str(held_subdir): ["4242"]})

        _, data = self.gc()
        self.assertEqual(data["mode"], "dry-run")
        self.assertEqual(json.loads(self.report.read_text()), data)
        by_path = {row["worktree"]: row for row in data["worktrees"]}
        self.assertIn("root-checkout", by_path[str(self.repo.resolve())]["reasons"])
        self.assertIn("non-archived-session-cwd", by_path[str(active.resolve())]["reasons"])
        self.assertIn("non-archived-session-cwd", by_path[str(active_alternate.resolve())]["reasons"])
        self.assertIn("authoritative-active-child", by_path[str(active_child.resolve())]["reasons"])
        self.assertIn("shared-session-cwd", by_path[str(shared.resolve())]["reasons"])
        self.assertIn("cwd-holder", by_path[str(held.resolve())]["reasons"])
        self.assertIn("dirty-worktree", by_path[str(dirty.resolve())]["reasons"])
        self.assertIn("unreachable-detached-head", by_path[str(detached.resolve())]["reasons"])
        self.assertIn("unowned-worktree-too-recent", by_path[str(recent.resolve())]["reasons"])
        self.assertEqual(by_path[str(eligible.resolve())]["classification"], "stale-clean-worktree")
        self.assertTrue(eligible.exists(), "dry-run must never remove a worktree")

    def test_apply_requires_prior_dry_run_is_bounded_and_retains_branches(self):
        one = self.add_branch("one"); two = self.add_branch("two")
        self.manifest("one", "worker", one, archived=True)
        self.manifest("two", "worker", two, archived=True)
        original_branches = {"refs/heads/lane-one", "refs/heads/lane-two"}

        refused, refusal = self.gc("--apply", "--max-remove", "1", ok=False)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refusal["applyRefusal"], "fresh-complete-dry-run-report-required")
        self.assertTrue(one.exists()); self.assertTrue(two.exists())

        self.gc()  # mandatory durable dry run
        _, applied = self.gc("--apply", "--max-remove", "1")
        self.assertEqual(applied["summary"]["removed"], 1)
        self.assertEqual(applied["summary"]["deferred"], 1)
        self.assertEqual(sum(path.exists() for path in (one, two)), 1)
        for ref in original_branches:
            self.git("show-ref", "--verify", "--quiet", ref)
        self.assertTrue((self.sessions / "one" / "session.jsonl").exists())
        self.assertTrue((self.sessions / "two" / "session.jsonl").exists())

    def test_reachable_detached_head_is_preserved_and_cwd_scan_failure_is_fail_closed(self):
        detached = self.add_detached("reachable")
        self.manifest("detached", "worker", detached, archived=True)
        _, ready = self.gc()
        row = next(row for row in ready["worktrees"] if row["worktree"] == str(detached.resolve()))
        self.assertEqual(row["classification"], "stale-clean-worktree")
        self.assertTrue(row["preservingRefs"])

        self.env["CRAFT_GC_CWD_HOLDERS_JSON"] = "not-json"
        _, blocked = self.gc()
        row = next(row for row in blocked["worktrees"] if row["worktree"] == str(detached.resolve()))
        self.assertIn("cwd-holder-scan-unavailable", row["reasons"])
        self.assertFalse(blocked["observationComplete"])


class LifecycleDebtTests(Base):
    def test_false_zero_backlog_exposes_all_other_debt_and_is_not_clean(self):
        self.manifest("terminal", "worker", status="needs-review")
        self.manifest("orphan", "worker")
        self.manifest("stale-coord", "coordinator")
        self.manifest("archived-child", "worker", archived=True)
        leases = self.runtime / "worker-leases"
        self.put(leases / "terminal.json", {"sessionId": "terminal", "state": "handoff-ready",
                                             "preservationState": "unknown", "parentSessionId": "coord"})
        self.put(leases / "orphan.json", {"sessionId": "orphan", "state": "stalled",
                                           "preservationState": "unknown", "parentSessionId": "dead-coord",
                                           "createdAt": self.now - 100_000_000})
        self.put(self.runtime / "coordinators" / "demo.json", {
            "project": "demo", "state": "authoritative", "coordinatorSessionId": "coord",
            "activeChildren": ["archived-child", "absent-child"]})
        self.put(self.report, {"schemaVersion": 1, "protocolVersion": "3.4.39", "mode": "dry-run",
                               "observationComplete": True, "worktrees": [{
                                   "classification": "stale-clean-worktree", "state": "candidate",
                                   "repository": "/repo", "worktree": "/repo/.worktrees/old",
                                   "head": "a" * 40, "ownership": "unowned", "ageMs": 99}]})

        _, report = self.cli("worker-lease.py", "report")
        self.assertEqual(report["archivableBacklog"], 0)
        self.assertFalse(report["lifecycleClean"])
        debt = report["lifecycleDebt"]
        self.assertFalse(debt["clean"])
        self.assertEqual(set(debt["classes"]), {
            "preservation-proven-archivable", "terminal-unknown", "orphaned-dead",
            "stale-coordinator", "archived-active-child", "stale-clean-worktree"})
        self.assertEqual(debt["summary"]["terminal-unknown"], 1)
        self.assertEqual(debt["summary"]["orphaned-dead"], 1)
        self.assertEqual(debt["summary"]["stale-coordinator"], 1)
        self.assertEqual(debt["summary"]["archived-active-child"], 2)
        self.assertEqual(debt["summary"]["stale-clean-worktree"], 1)

    def test_archivable_compatibility_and_missing_gc_observation_prevent_clean(self):
        self.manifest("done", "worker", status="needs-review")
        self.put(self.runtime / "worker-leases" / "done.json", {
            "sessionId": "done", "state": "handoff-ready", "preservationState": "pushed"})
        _, report = self.cli("worker-lease.py", "report")
        self.assertEqual(report["archivableBacklog"], 1)
        self.assertEqual(report["lifecycleDebt"]["summary"]["preservation-proven-archivable"], 1)
        self.assertFalse(report["lifecycleDebt"]["observationComplete"])
        self.assertIn("stale-clean-worktree:not-observed", report["lifecycleDebt"]["unknown"])


class RegistryAndInstallerTests(Base):
    def test_reconcile_activity_prunes_archived_and_absent_children_but_retains_every_non_archived_lane(self):
        coord_cwd = self.root / "repo"; coord_cwd.mkdir()
        labels = ["coordinators", "project::demo", "protocol-version::3.4.39"]
        self.manifest("coord", "coordinator", coord_cwd, labels=labels,
                      name="[demo] Coordinator v3.4.39", projectId="native",
                      llmConnection="chatgpt-plus", model="pi/gpt-5.6-sol", permissionMode="execute")
        self.manifest("live", "worker", status="in_progress")
        self.manifest("needs-review", "auditor", status="needs-review")
        self.manifest("done-but-unarchived", "worker", status="done")
        self.manifest("archived", "worker", archived=True)
        self.put(self.runtime / "reporting-policy.json", {"schemaVersion": 1, "mode": "pull-only",
            "ownerFacingSessionId": "owner", "configuredAt": 1, "interception": "unavailable",
            "detection": "best-effort-session-transcript"})
        self.put(self.runtime / "coordinators" / "demo.json", {
            "schemaVersion": 1, "project": "demo", "projectId": "native", "state": "authoritative",
            "coordinatorSessionId": "coord", "generation": 1, "lastHeartbeatAt": self.now - 1000,
            "leaseExpiresAt": self.now + 60_000,
            "activeChildren": ["needs-review", "absent", "live", "archived", "live", "done-but-unarchived"]})

        _, result = self.cli("coordinator-registry.py", "reconcile-activity", "--apply")
        saved = json.loads((self.runtime / "coordinators" / "demo.json").read_text())
        self.assertEqual(saved["activeChildren"], ["done-but-unarchived", "live", "needs-review"])
        reasons = {(row["sessionId"], row["reason"]) for row in result["rows"][0]["prunedActiveChildren"]}
        self.assertEqual(reasons, {("absent", "absent"), ("archived", "archived"), ("live", "duplicate")})

    def test_reconcile_activity_prunes_stale_children_even_when_authoritative_owner_is_absent(self):
        self.manifest("live", "worker", status="done")
        self.manifest("archived", "worker", archived=True)
        self.put(self.runtime / "coordinators" / "demo.json", {
            "schemaVersion": 1, "project": "demo", "state": "authoritative",
            "coordinatorSessionId": "missing-owner", "generation": 2,
            "activeChildren": ["missing", "live", "archived"]})
        _, result = self.cli("coordinator-registry.py", "reconcile-activity", "--apply")
        saved = json.loads((self.runtime / "coordinators" / "demo.json").read_text())
        self.assertEqual(saved["activeChildren"], ["live"])
        self.assertEqual(result["rows"][0]["admissionBlocker"], "owner-not-live")

    def test_installer_and_manifest_generator_include_gc_payload(self):
        installer = (ROOT / "install.sh").read_text()
        readme = (ROOT / "README.md").read_text()
        generator = (ROOT / "tools" / "generate-manifest.sh").read_text()
        self.assertIn("post-archive-reaper.py worktree-gc.py", installer)
        self.assertIn("  worktree-gc.py", readme)
        self.assertIn("scripts config skills tests docs tools", generator)
        # manifest.sha256 is regenerated at release validation; this assertion
        # catches an installer payload that is not package-hash covered.
        manifest = (ROOT / "manifest.sha256").read_text()
        self.assertIn("  scripts/worktree-gc.py", manifest)


if __name__ == "__main__":
    unittest.main()

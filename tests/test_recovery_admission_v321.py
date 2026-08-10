# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts")) / "recovery-admission.py"
NOW = 1786298100000
TOKEN = "never-print-this-test-token"

FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state_path = Path(sys.argv[1])
raw_args = sys.argv[2:]
state = json.loads(state_path.read_text())
state["allJson"] = state.get("allJson", True) and bool(raw_args) and raw_args[0] == "--json"
args = raw_args[1:] if raw_args[:1] == ["--json"] else raw_args
state.setdefault("records", []).append(args)
state["tokenMatched"] = state.get("tokenMatched", True) and os.environ.get("CRAFT_SERVER_TOKEN") == state["expectedToken"]
state["serverUrl"] = os.environ.get("CRAFT_SERVER_URL")
def save(): state_path.write_text(json.dumps(state))
def output(value): save(); print(json.dumps(value)); raise SystemExit(0)
if args == ["automation", "capabilities"]:
    if state.get("createKillSwitchAfterCapabilities"):
        Path(state["createKillSwitchAfterCapabilities"]).touch()
    output(state["capabilities"])
if args == ["workspaces"]:
    output(state["workspaces"])
if args[:2] == ["automation", "deliver"]:
    fields = {args[index]: args[index + 1] for index in range(2, len(args) - 1, 2) if args[index].startswith("--")}
    scope = "|".join(fields[name] for name in ("--workspace", "--session", "--matcher", "--action", "--occurrence", "--key"))
    state.setdefault("deliveryScopes", []).append(scope)
    if state.get("busyOnce") and not state.get("busySeen"):
        state["busySeen"] = True
        output({"status": "busy", "messageId": ""})
    if state.get("blocked"):
        output({"status": "blocked", "messageId": ""})
    if state.get("error"):
        output({"status": "error", "messageId": ""})
    receipt = state.setdefault("receipts", {}).get(scope)
    if receipt:
        output({"status": "duplicate", "messageId": receipt})
    receipt = "msg-" + str(len(state["receipts"]) + 1)
    state["receipts"][scope] = receipt
    if state.get("crashAfterReceipt") and not state.get("crashed"):
        state["crashed"] = True
        save()
        raise SystemExit(9)
    output({"status": "delivered", "messageId": receipt})
save()
print(json.dumps({"status": "error"}))
raise SystemExit(2)
'''


class RecoveryAdmissionV321Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.runtime = self.root / "runtime"
        self.sessions = self.workspace / "sessions"
        self.config = self.workspace / "automations.json"
        self.harness = self.root / "controller-harness.py"
        self.harness.write_text('#!/bin/sh\necho \'{"healthy":true,"rows":[{"sessionId":"controller","sessionRole":"recovery-controller","state":"active"}]}\'\n')
        self.harness.chmod(0o755)
        self.fake_cli = self.root / "fake-craft-cli.py"
        self.fake_cli.write_text(FAKE_CLI)
        self.fake_cli.chmod(0o755)
        self.fake_state = self.root / "fake-cli-state.json"
        self.put(self.fake_state, {"expectedToken": TOKEN,
                                  "capabilities": {"available": True, "version": 1, "deliverChannel": "automations:admissionDeliver",
                                                   "runtimeVersion": "2026.8.10", "runtimeCommit": "runtime-commit-1"},
                                  "workspaces": [{"id": "workspace-7", "rootPath": str(self.workspace)}]})
        self.env = {**os.environ, "CRAFT_WORKSPACE": str(self.workspace), "CRAFT_RUNTIME": str(self.runtime),
                    "CRAFT_SESSIONS": str(self.sessions), "CRAFT_TEST_NOW_MS": str(NOW),
                    "CRAFT_CONTROLLER_HARNESS": str(self.harness), "CRAFT_RPC_CLI": f"{sys.executable} {self.fake_cli} {self.fake_state}",
                    "CRAFT_SERVER_TOKEN": TOKEN, "CRAFT_SERVER_URL": "https://craft.example.test",
                    "CRAFT_WORKSPACE_ID": "workspace-7", "CRAFT_EXPECTED_RUNTIME_VERSION": "2026.8.10",
                    "CRAFT_EXPECTED_RUNTIME_COMMIT": "runtime-commit-1"}
        self.put(self.config, {"version": 2, "automations": {"SchedulerTick": [{"id": "a321-notifier", "enabled": False,
                 "actions": [{"type": "prompt", "prompt": "disabled"}]}]}})
        self.manifest("controller")

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + "\n")

    def fake(self):
        return json.loads(self.fake_state.read_text())

    def mutate_fake(self, **changes):
        row = self.fake()
        row.update(changes)
        self.put(self.fake_state, row)

    def manifest(self, sid, role="recovery-controller", mode="persistent", archived=False, status="todo"):
        labels = [f"agent-role::{role}"]
        if mode:
            labels.append(f"controller-mode::{mode}")
        self.put(self.sessions / sid / "session.jsonl", {"id": sid, "labels": labels, "isArchived": archived,
                                                           "sessionStatus": status, "workspaceRootPath": str(self.workspace)})

    def incident(self, iid="i1", kind="coordinator-lease-stale", state="open", session="coord", project=None, work_unit=None):
        row = {"incidentId": iid, "kind": kind, "state": state, "sessionId": session, "severity": "high", "firstSeenAt": 1,
               "evidenceFingerprint": "ef" + iid}
        if project:
            row["project"] = project
        if work_unit:
            row["workUnit"] = work_unit
        self.put(self.runtime / "recovery-incidents" / f"{iid}.json", row)

    def cli(self, *args, ok=True, env=None):
        cp = subprocess.run([sys.executable, str(TOOL), *args], env=env or self.env, text=True, capture_output=True)
        if ok and cp.returncode:
            self.fail(cp.stdout + cp.stderr)
        return cp, json.loads(cp.stdout)

    def apply(self, *, ok=True, env=None):
        return self.cli("tick", "--controller-session", "controller", "--apply", ok=ok, env=env)

    def delivery_calls(self):
        return [record for record in self.fake().get("records", []) if record[:2] == ["automation", "deliver"]]

    def state(self):
        return json.loads((self.runtime / "self-healing" / "admission.json").read_text())

    def test_report_only_never_calls_cli_or_creates_state(self):
        self.incident()
        _, row = self.cli("report")
        self.assertEqual(row["actionableCount"], 1)
        self.assertEqual(self.fake().get("records"), None)
        self.assertFalse((self.runtime / "self-healing" / "admission.json").exists())

    def test_no_incident_does_not_deliver_or_mutate_automation_config(self):
        before = self.config.read_bytes()
        _, row = self.apply()
        self.assertEqual(row["reason"], "no-actionable-incidents")
        self.assertEqual(self.delivery_calls(), [])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.runtime / "self-healing" / "admission.json").exists())

    def test_absent_runtime_identity_hard_blocks_without_delivery(self):
        self.incident()
        self.mutate_fake(capabilities={"available": True, "version": 1, "deliverChannel": "automations:admissionDeliver"})
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(row["state"]["phase"], "blocked")
        self.assertEqual(self.delivery_calls(), [])

    def test_missing_workspace_id_fails_closed_without_delivery(self):
        self.incident()
        env = {key: value for key, value in self.env.items() if key != "CRAFT_WORKSPACE_ID"}
        cp, row = self.apply(ok=False, env=env)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("workspace ID", row["error"])
        self.assertEqual(self.delivery_calls(), [])

    def test_workspace_id_and_controller_manifest_are_bound_to_configured_root(self):
        self.incident()
        self.mutate_fake(workspaces=[{"id": "workspace-7", "rootPath": str(self.root / "other-workspace")}])
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("not bound", row["state"]["reason"])
        self.assertEqual(self.delivery_calls(), [])

    def test_kill_switch_created_during_discovery_wins_before_delivery(self):
        self.incident()
        switch = self.runtime / "self-healing.disabled"
        self.mutate_fake(createKillSwitchAfterCapabilities=str(switch))
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(row["state"]["reason"], "kill-switch-active-before-delivery")
        self.assertEqual(self.delivery_calls(), [])

    def test_mismatched_runtime_version_hard_blocks_without_delivery(self):
        self.incident()
        self.mutate_fake(capabilities={"available": True, "version": 1, "deliverChannel": "automations:admissionDeliver",
                                      "runtimeVersion": "2026.8.9", "runtimeCommit": "runtime-commit-1"})
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(row["state"]["phase"], "blocked")
        self.assertIn("runtime identity", row["state"]["reason"])
        self.assertEqual(self.delivery_calls(), [])

    def test_mismatched_runtime_commit_hard_blocks_without_delivery(self):
        self.incident()
        self.mutate_fake(capabilities={"available": True, "version": 1, "deliverChannel": "automations:admissionDeliver",
                                      "runtimeVersion": "2026.8.10", "runtimeCommit": "other-commit"})
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(row["state"]["phase"], "blocked")
        self.assertEqual(self.delivery_calls(), [])

    def test_absent_capability_hard_blocks_without_delivery(self):
        self.incident()
        self.mutate_fake(capabilities={"available": False, "version": 1, "deliverChannel": "automations:admissionDeliver"})
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(row["state"]["phase"], "blocked")
        self.assertEqual(self.delivery_calls(), [])

    def test_delivers_only_to_exact_persistent_target_with_stable_scope(self):
        self.incident("late", session="coord-a")
        self.incident("first", session="coord-b")
        before_config = self.config.read_bytes()
        before_controller_manifest = (self.sessions / "controller" / "session.jsonl").read_bytes()
        before_sessions = sorted(path.name for path in self.sessions.iterdir())
        _, row = self.apply()
        direct = row["state"]["directDelivery"]
        self.assertEqual(row["state"]["phase"], "notified")
        self.assertEqual(row["state"]["notifierSessionId"], None)
        self.assertEqual(direct["controllerSessionId"], "controller")
        self.assertEqual(direct["workspaceId"], "workspace-7")
        self.assertEqual(len(self.delivery_calls()), 1)
        call = self.delivery_calls()[0]
        self.assertEqual(call[call.index("--session") + 1], "controller")
        self.assertEqual(call[call.index("--workspace") + 1], "workspace-7")
        self.assertEqual(self.config.read_bytes(), before_config)
        self.assertEqual((self.sessions / "controller" / "session.jsonl").read_bytes(), before_controller_manifest)
        self.assertEqual(sorted(path.name for path in self.sessions.iterdir()), before_sessions)
        self.assertTrue(self.fake()["tokenMatched"])
        self.assertEqual(self.fake()["serverUrl"], "https://craft.example.test")
        self.assertNotIn(["versions"], self.fake()["records"])

    def test_notified_cycle_rearms_after_cooldown_with_fresh_scope(self):
        self.incident()
        _, first = self.apply()
        first_scope = first["state"]["scope"]
        first_message = first["state"]["directDelivery"]["messageId"]
        _, cooling = self.apply()
        self.assertEqual(cooling["reason"], "fingerprint-cooldown")
        self.assertEqual(len(self.delivery_calls()), 1)
        later = dict(self.env)
        later["CRAFT_TEST_NOW_MS"] = str(NOW + 900_001)
        _, second = self.apply(env=later)
        self.assertEqual(second["state"]["phase"], "notified")
        self.assertNotEqual(second["state"]["scope"], first_scope)
        self.assertNotEqual(second["state"]["directDelivery"]["messageId"], first_message)
        self.assertEqual(len(self.delivery_calls()), 2)
        self.assertEqual(len(self.fake()["receipts"]), 2)

    def test_repeated_and_simultaneous_apply_have_one_receipt(self):
        self.incident()
        outcomes = []
        def run():
            outcomes.append(self.apply(ok=False)[0].returncode)
        threads = [threading.Thread(target=run), threading.Thread(target=run)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertIn(0, outcomes)
        self.assertTrue(all(code in {0, 75} for code in outcomes))
        self.apply()
        self.assertEqual(len(self.delivery_calls()), 1)
        self.assertEqual(len(self.fake()["receipts"]), 1)
        self.assertEqual(self.state()["directDelivery"]["messageId"], "msg-1")

    def test_crash_prepared_replay_returns_original_message_receipt(self):
        self.incident()
        self.mutate_fake(crashAfterReceipt=True)
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 75)
        self.assertEqual(row["state"]["phase"], "prepared")
        prepared_scope = row["state"]["scope"]
        self.mutate_fake(crashAfterReceipt=False)
        _, row = self.apply()
        self.assertEqual(row["state"]["phase"], "notified")
        self.assertEqual(row["state"]["directDelivery"]["status"], "duplicate")
        self.assertEqual(row["state"]["directDelivery"]["messageId"], "msg-1")
        self.assertEqual(row["state"]["scope"], prepared_scope)
        self.assertEqual(len(self.fake()["receipts"]), 1)

    def test_busy_is_retriable_with_same_prepared_scope(self):
        self.incident()
        self.mutate_fake(busyOnce=True)
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 75)
        self.assertEqual(row["state"]["phase"], "prepared")
        scope = row["state"]["scope"]
        _, row = self.apply()
        self.assertEqual(row["state"]["phase"], "notified")
        self.assertEqual(row["state"]["scope"], scope)
        self.assertEqual(len(self.delivery_calls()), 2)

    def test_server_blocked_response_hard_blocks(self):
        self.incident()
        self.mutate_fake(blocked=True)
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(row["state"]["phase"], "blocked")
        self.assertEqual(len(self.delivery_calls()), 1)

    def test_server_error_response_hard_blocks(self):
        self.incident()
        self.mutate_fake(error=True)
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(row["state"]["phase"], "blocked")
        self.assertEqual(len(self.delivery_calls()), 1)

    def test_token_never_appears_in_admission_output_or_state(self):
        self.incident()
        cp, row = self.apply()
        material = cp.stdout + json.dumps(row) + (self.runtime / "self-healing" / "admission.json").read_text()
        self.assertNotIn(TOKEN, material)
        self.assertTrue(self.fake()["tokenMatched"])
        self.assertTrue(self.fake()["allJson"])

    def test_kill_switch_and_owner_gate_remain_authoritative(self):
        self.incident("g", kind="owner-gate-blocked")
        _, row = self.apply()
        self.assertEqual(row["reason"], "no-actionable-incidents")
        self.incident()
        (self.runtime / "self-healing.disabled").parent.mkdir(parents=True, exist_ok=True)
        (self.runtime / "self-healing.disabled").touch()
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(row["reason"], "kill-switch-active")
        self.assertEqual(self.delivery_calls(), [])
        bypass = subprocess.run([sys.executable, str(TOOL), "tick", "--controller-session", "controller",
                                 "--apply", "--ignore-kill-switch"], env=self.env, text=True, capture_output=True)
        self.assertEqual(bypass.returncode, 2)
        self.assertIn("unrecognized arguments", bypass.stderr)
        self.assertEqual(self.delivery_calls(), [])

    def test_requires_exact_live_persistent_controller(self):
        self.incident()
        self.manifest("controller", mode=None)
        cp, row = self.apply(ok=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("not marked persistent", row["error"])
        self.assertEqual(self.delivery_calls(), [])


class RecoveryAdmissionCronV321Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.craft = self.root / ".craft-agent"
        self.runtime = self.craft / "runtime" / "self-healing"
        self.scripts = self.craft / "scripts"
        self.scripts.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        self.capture = self.root / "capture.json"
        self.rpc_cli = self.root / "craft-cli"
        self.rpc_cli.write_text("#!/bin/sh\nexit 0\n")
        self.rpc_cli.chmod(0o700)
        (self.scripts / "recovery-admission.py").write_text(
            "import json,os,sys\n"
            "open(os.environ['CRAFT_TEST_CAPTURE'],'w').write(json.dumps({"
            "'rpcCli':os.environ.get('CRAFT_RPC_CLI'),'serverUrl':os.environ.get('CRAFT_SERVER_URL'),'args':sys.argv[1:]}))\n"
        )
        self.config = self.runtime / "persistent-controller.json"
        self.value = {"sessionId": "controller", "workspaceId": "workspace-7",
                      "expectedRuntimeVersion": "2026.8.10", "expectedRuntimeCommit": "commit-1",
                      "serverUrl": "wss://craft.example.test:9100", "rpcCli": str(self.rpc_cli)}
        self.config.write_text(json.dumps(self.value))
        self.env = {**os.environ, "HOME": str(self.root), "CRAFT_HOME": str(self.craft),
                    "CRAFT_PYTHON": sys.executable, "CRAFT_TEST_CAPTURE": str(self.capture)}
        self.cron = ROOT / "scripts" / "recovery-admission-cron.sh"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cron(self):
        return subprocess.run(["/bin/zsh", str(self.cron)], env=self.env, text=True, capture_output=True)

    def test_periodic_launcher_exports_exact_cli_and_secure_server(self):
        cp = self.run_cron()
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        row = json.loads(self.capture.read_text())
        self.assertEqual(row["rpcCli"], str(self.rpc_cli))
        self.assertEqual(row["serverUrl"], "wss://craft.example.test:9100")
        self.assertIn("--apply", row["args"])

    def test_periodic_launcher_refuses_missing_cli_or_insecure_remote_ws(self):
        for key, value in (("rpcCli", None), ("serverUrl", "ws://craft.example.test:9100")):
            with self.subTest(key=key):
                row = dict(self.value)
                if value is None:
                    row.pop(key)
                else:
                    row[key] = value
                self.config.write_text(json.dumps(row))
                self.capture.unlink(missing_ok=True)
                cp = self.run_cron()
                self.assertEqual(cp.returncode, 2)
                self.assertFalse(self.capture.exists())


if __name__ == "__main__":
    unittest.main()

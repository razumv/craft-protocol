# SPDX-License-Identifier: Apache-2.0
"""Owner-gate board bridge suite (Protocol v3.4.10)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts"))
BOARD = SCRIPTS / "owner-gate-board.py"
GATE = SCRIPTS / "owner-gate.py"

FAKE_CLI = r'''#!/usr/bin/env python3
import json, sys
from pathlib import Path
log = Path(__file__).with_name("cli-log.jsonl")
args = sys.argv[1:]
with log.open("a") as fh:
    fh.write(json.dumps(args) + "\n")
if args[:3] == ["--json", "session", "create"]:
    counter = Path(__file__).with_name("card-counter")
    n = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(n))
    print(json.dumps({"id": f"card-{n}"}))
else:
    print("{}")
'''


class GateBoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"; self.sessions = self.root / "sessions"
        self.sessions.mkdir(parents=True)
        self.fake_cli = self.root / "fake-cli.py"
        self.fake_cli.write_text(FAKE_CLI); self.fake_cli.chmod(0o755)
        self.env = {**os.environ, "CRAFT_RUNTIME": str(self.runtime),
                    "CRAFT_SESSIONS": str(self.sessions), "CRAFT_WORKSPACE": str(self.root),
                    "CRAFT_RPC_CLI": str(self.fake_cli)}

    def tearDown(self): self.tmp.cleanup()

    def cli_calls(self):
        log = self.root / "cli-log.jsonl"
        if not log.exists(): return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def run_tool(self, script, *args, ok=True):
        cp = subprocess.run([sys.executable, str(script), *args], env=self.env,
                            text=True, capture_output=True, timeout=30)
        if ok and cp.returncode:
            self.fail(cp.stdout + cp.stderr)
        return cp, (json.loads(cp.stdout) if cp.returncode == 0 and cp.stdout else None)

    def make_gate(self, gate="ship-decision", choices="SHIP,WAIT",
                  category="human-product-judgment-action"):
        self.run_tool(GATE, "create", "--project", "demo", "--gate", gate,
                      "--question", "Ship it?", "--choices", choices,
                      "--owner-only-category", category, "--scope", "work-unit")

    def owner_types(self, session_id, text, ts=None):
        folder = self.sessions / session_id; folder.mkdir(exist_ok=True)
        path = folder / "session.jsonl"
        if not path.exists():
            path.write_text(json.dumps({"id": session_id}) + "\n")
        with path.open("a") as fh:
            fh.write(json.dumps({"type": "user", "timestamp": ts or int(time.time() * 1000) + 60_000,
                                 "content": text}) + "\n")

    def board(self):
        return json.loads((self.runtime / "owner-gate-board.json").read_text())

    def test_open_gate_gets_card_and_typed_choice_resolves_it(self):
        self.make_gate()
        _, out = self.run_tool(BOARD, "sync", "--apply")
        self.assertEqual([a["action"] for a in out["actions"]], ["create-card"])
        card = self.board()["cards"]["demo::ship-decision"]
        self.assertEqual(card["sessionId"], "card-1")
        # Notes and flag are set on the created card.
        kinds = [c[1] for c in self.cli_calls() if c[0] == "invoke"]
        self.assertIn("sessions:setNotes", kinds)
        # The owner types the exact choice into the card.
        self.owner_types("card-1", "SHIP")
        _, out = self.run_tool(BOARD, "sync", "--apply")
        self.assertIn("resolve", [a["action"] for a in out["actions"]])
        _, gates = self.run_tool(GATE, "list", "--project", "demo")
        gate = gates["gates"][0]
        self.assertEqual(gate["state"], "resolved")
        self.assertEqual(gate["choice"], "SHIP")
        self.assertEqual(gate["authority"], "direct-owner")
        self.assertIn("card-1", gate["authorityEvidence"])
        # Card completed and archived; mapping dropped.
        archived = [c for c in self.cli_calls() if c[0] == "invoke" and '"archive"' in c[3]]
        self.assertTrue(archived)
        self.assertEqual(self.board()["cards"], {})

    def test_unrecognized_and_ambiguous_choices_never_resolve(self):
        self.make_gate(choices="SHIP,ship-later")
        self.run_tool(BOARD, "sync", "--apply")
        self.owner_types("card-1", "да, давай")
        _, out = self.run_tool(BOARD, "sync", "--apply")
        self.assertIn("unrecognized-choice", [a["action"] for a in out["actions"]])
        _, gates = self.run_tool(GATE, "list", "--project", "demo")
        self.assertEqual(gates["gates"][0]["state"], "open")

    def test_project_hold_requires_exact_resume(self):
        self.run_tool(GATE, "hold", "--project", "demo", "--reason", "pause")
        self.run_tool(BOARD, "sync", "--apply")
        self.owner_types("card-1", "resume")  # wrong case — refused
        _, out = self.run_tool(BOARD, "sync", "--apply")
        self.assertIn("unrecognized-choice", [a["action"] for a in out["actions"]])
        self.owner_types("card-1", "RESUME")
        _, out = self.run_tool(BOARD, "sync", "--apply")
        self.assertIn("resolve", [a["action"] for a in out["actions"]])
        _, gates = self.run_tool(GATE, "list", "--project", "demo")
        self.assertEqual(gates["gates"][0]["state"], "resolved")

    def test_gate_resolved_elsewhere_completes_card(self):
        self.make_gate()
        self.run_tool(BOARD, "sync", "--apply")
        self.run_tool(GATE, "resolve", "--project", "demo", "--gate", "ship-decision",
                      "--choice", "WAIT", "--authority", "direct-owner", "--evidence", "cli")
        _, out = self.run_tool(BOARD, "sync", "--apply")
        self.assertIn("complete-card", [a["action"] for a in out["actions"]])
        self.assertEqual(self.board()["cards"], {})


if __name__ == "__main__":
    unittest.main()

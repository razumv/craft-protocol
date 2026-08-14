# SPDX-License-Identifier: Apache-2.0
"""Increment board projection with story subtasks (Protocol v3.4.20)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get("CRAFT_TEST_SCRIPTS", ROOT / "scripts"))
BOARD = SCRIPTS / "increment-board.py"

FAKE_CLI = r'''#!/usr/bin/env python3
import json, sys
from pathlib import Path
log = Path(__file__).with_name("cli-log.jsonl")
args = sys.argv[1:]
with log.open("a") as fh:
    fh.write(json.dumps(args) + "\n")
if args[:2] == ["invoke", "sessions:create"]:
    counter = Path(__file__).with_name("card-counter")
    n = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(n))
    print(json.dumps({"id": f"card-{n}"}))
else:
    print("{}")
'''

# The board never talks to a coordinator; it renders whatever status reported.
FAKE_STATUS = r'''#!/usr/bin/env python3
import json, os
from pathlib import Path
print(Path(os.environ["CRAFT_BOARD_FIXTURE"]).read_text())
'''


class IncrementBoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"; self.sessions = self.root / "sessions"
        self.scripts = self.root / "scripts"
        self.sessions.mkdir(parents=True); self.scripts.mkdir(parents=True)
        self.runtime.mkdir(parents=True)
        self.fake_cli = self.root / "fake-cli.py"
        self.fake_cli.write_text(FAKE_CLI); self.fake_cli.chmod(0o755)
        status = self.scripts / "coordinator-status.py"
        status.write_text(FAKE_STATUS); status.chmod(0o755)
        self.fixture = self.root / "status.json"
        self.env = {**os.environ, "CRAFT_RUNTIME": str(self.runtime),
                    "CRAFT_SESSIONS": str(self.sessions), "CRAFT_SCRIPTS": str(self.scripts),
                    "CRAFT_RPC_CLI": str(self.fake_cli), "CRAFT_WORKSPACE_ID": "ws-test",
                    "CRAFT_BOARD_FIXTURE": str(self.fixture),
                    "CRAFT_BOARD_MODEL": "pi/gpt-5.4-mini",
                    "CRAFT_BOARD_CONNECTION": "chatgpt-plus"}

    def tearDown(self): self.tmp.cleanup()

    def report(self, stories, *, increment_id="pi-1", stage="building"):
        self.fixture.write_text(json.dumps({"projects": [{
            "project": "demo", "classification": "verified",
            "synthesized": {"activeWorkerCount": 1, "openGateCount": 0},
            "declared": {"objective": "Customer sees subscriptions", "phase": "executing",
                         "productIncrement": {"id": increment_id, "stage": stage, "riskTier": "medium",
                                              "demonstrationCriterion": "Open a Person",
                                              "stories": stories}}}]}))

    def calls(self):
        log = self.root / "cli-log.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def creates(self):
        return [json.loads(c[3]) for c in self.calls() if c[:2] == ["invoke", "sessions:create"]]

    def commands(self, kind):
        out = []
        for call in self.calls():
            if call[:2] == ["invoke", "sessions:command"]:
                body = json.loads(call[3])
                if body.get("type") == kind:
                    out.append((json.loads(call[2]), body))
        return out

    def board(self):
        return json.loads((self.runtime / "increment-board.json").read_text())

    def run_board(self, *args):
        cp = subprocess.run([sys.executable, str(BOARD), *args], env=self.env,
                            text=True, capture_output=True, timeout=60)
        if cp.returncode:
            self.fail(cp.stdout + cp.stderr)
        return json.loads(cp.stdout) if cp.stdout else None

    def story(self, sid, state, title=None):
        return {"id": sid, "title": title or sid.title(), "state": state,
                "dependsOn": [], "riskContribution": "low"}

    def test_each_story_becomes_a_subtask_of_the_increment_card(self):
        # `0/5` on a card tells the owner nothing about what the five are.
        self.report([self.story("s1", "accepted"), self.story("s2", "executing"),
                     self.story("s3", "ready")])
        out = self.run_board("--apply")
        self.assertIn("create:demo", out["actions"])
        self.assertIn("story-create:demo/s1", out["actions"])
        card = self.board()["incrementCards"]["demo"]
        self.assertEqual(card["sessionId"], "card-1")
        self.assertEqual(sorted(card["stories"]), ["s1", "s2", "s3"])
        # Every story session is created as a subtask of the card itself.
        story_creates = self.creates()[1:]
        self.assertEqual({c["parentSessionId"] for c in story_creates}, {"card-1"})
        # The card counts accepted stories so the title matches the rows below it.
        renames = dict(self.commands("rename"))
        self.assertIn("1/3", renames["card-1"]["name"])
        # Board status is derived from story state, never guessed.
        statuses = {sid: body["state"] for sid, body in self.commands("setSessionStatus")}
        self.assertEqual(statuses["card-2"], "done")
        self.assertEqual(statuses["card-3"], "in_progress")
        self.assertEqual(statuses["card-4"], "todo")

    def test_story_state_changes_update_the_existing_subtask(self):
        self.report([self.story("s1", "executing")])
        self.run_board("--apply")
        before = len(self.creates())
        self.report([self.story("s1", "accepted")])
        self.run_board("--apply")
        # No duplicate card: the same subtask is restated, not recreated.
        self.assertEqual(len(self.creates()), before)
        last_status = self.commands("setSessionStatus")[-1]
        self.assertEqual(last_status, ("card-2", {"type": "setSessionStatus", "state": "done"}))
        self.assertTrue(dict(self.commands("rename"))["card-2"]["name"].startswith("✅"))

    def test_a_story_that_leaves_the_increment_is_archived(self):
        self.report([self.story("s1", "ready"), self.story("s2", "ready")])
        self.run_board("--apply")
        self.report([self.story("s1", "ready")])
        out = self.run_board("--apply")
        self.assertIn("story-archive:demo/s2", out["actions"])
        self.assertIn("card-3", [sid for sid, _ in self.commands("archive")])
        self.assertEqual(sorted(self.board()["incrementCards"]["demo"]["stories"]), ["s1"])

    def test_a_new_increment_rolls_the_whole_story_set(self):
        self.report([self.story("s1", "accepted")], increment_id="pi-1")
        self.run_board("--apply")
        self.report([self.story("t1", "ready")], increment_id="pi-2")
        out = self.run_board("--apply")
        self.assertIn("increment-rolled:demo", out["actions"])
        self.assertEqual(sorted(self.board()["incrementCards"]["demo"]["stories"]), ["t1"])

    def test_reset_archives_cards_and_subtasks_then_rebuilds(self):
        self.report([self.story("s1", "ready")])
        self.run_board("--apply")
        out = self.run_board("--apply", "--reset-cards")
        self.assertIn("reset:demo", out["actions"])
        archived = [sid for sid, _ in self.commands("archive")]
        self.assertIn("card-1", archived)
        self.assertIn("card-2", archived)
        # The rebuild is part of the same pass, so the owner is never left cardless.
        self.assertEqual(self.board()["incrementCards"]["demo"]["sessionId"], "card-3")

    def test_owner_archiving_the_card_takes_its_subtasks_with_it(self):
        self.report([self.story("s1", "ready")])
        self.run_board("--apply")
        (self.sessions / "card-1").mkdir()
        (self.sessions / "card-1" / "session.jsonl").write_text(
            json.dumps({"id": "card-1", "isArchived": True}) + "\n")
        out = self.run_board("--apply")
        self.assertIn("card-archived-by-owner:demo", out["actions"])
        self.assertNotIn("demo", self.board()["incrementCards"])

    def test_dry_run_touches_nothing(self):
        self.report([self.story("s1", "ready")])
        out = self.run_board()
        self.assertEqual(out["actions"], [])
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()

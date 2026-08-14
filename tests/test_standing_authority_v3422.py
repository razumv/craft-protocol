# SPDX-License-Identifier: Apache-2.0
"""Standing owner authority for protected merges (Protocol v3.4.22)."""
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
TOOL = SCRIPTS / "standing-authority.py"
GATE = SCRIPTS / "owner-gate.py"


class StandingAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime"
        self.env = {**os.environ, "CRAFT_RUNTIME": str(self.runtime),
                    "CRAFT_SESSIONS": str(self.root / "sessions"),
                    "CRAFT_WORKSPACE": str(self.root)}
        self.now = int(time.time() * 1000)
        self.repo = self.root / "repo"
        self.git_init()

    def tearDown(self): self.tmp.cleanup()

    def git(self, *args, check=True):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=check,
                              capture_output=True, text=True,
                              env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"})

    def git_init(self):
        subprocess.run(["git", "init", "-q", "-b", "dev", str(self.repo)], check=True)
        (self.repo / "f").write_text("base")
        self.git("add", "-A"); self.git("commit", "-qm", "base")
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/dev", base)
        (self.repo / "f").write_text("candidate")
        self.git("commit", "-qam", "candidate")
        self.candidate = self.git("rev-parse", "HEAD").stdout.strip()
        self.base = base

    def cli(self, *args, ok=True):
        cp = subprocess.run([sys.executable, str(TOOL), *args], env=self.env,
                            text=True, capture_output=True, timeout=60)
        if ok and cp.returncode:
            self.fail(f"exit {cp.returncode}\n{cp.stdout}\n{cp.stderr}")
        return cp, (json.loads(cp.stdout) if cp.stdout else None)

    def certificate(self, **overrides):
        value = {"schemaVersion": 1, "project": "demo", "workUnit": "wu-1",
                 "storyId": "story-1", "candidateSha": self.candidate,
                 "auditedSha": self.candidate, "auditorSessionId": "auditor-1",
                 "auditVerdict": "PASS", "requiredCiRunIds": ["ci-1"],
                 "requiredCiAllSuccess": True, "mergeSha": self.candidate,
                 "mergedMainRunIds": ["readback-1"], "mergedMainAllSuccess": True,
                 "unresolvedGates": [], "headUnchanged": True}
        value.update(overrides)
        path = self.root / "cert.json"
        path.write_text(json.dumps(value))
        return path

    def grant(self, ok=True, **overrides):
        args = {"--project": "demo", "--branches": "dev", "--max-risk-tier": "medium",
                "--ttl-seconds": "604800", "--authority": "direct-owner",
                "--reason": "Owner authorises accepted-candidate merges into dev"}
        args.update(overrides)
        flat = [x for pair in args.items() for x in pair]
        return self.cli("grant", *flat, ok=ok)

    def check(self, *, work_unit="wu-1", branch="dev", risk="medium", cert=None, ok=True):
        return self.cli("check", "--project", "demo", "--work-unit", work_unit,
                        "--branch", branch, "--risk-tier", risk,
                        "--certificate", str(cert or self.certificate()),
                        "--repo", str(self.repo), ok=ok)

    def test_granted_authority_authorizes_a_fully_proven_merge(self):
        self.grant()
        cp, out = self.check()
        self.assertEqual(cp.returncode, 0)
        self.assertTrue(out["authorized"])
        self.assertEqual(out["refusals"], [])

    def test_without_a_grant_nothing_is_authorized(self):
        cp, out = self.check(ok=False)
        self.assertEqual(cp.returncode, 4)
        self.assertEqual(out["refusals"], ["no-standing-authority"])

    def test_authority_is_bound_to_the_exact_branch_and_risk_ceiling(self):
        self.grant(**{"--branches": "dev", "--max-risk-tier": "medium"})
        _, other = self.check(branch="main", ok=False)
        self.assertIn("branch-not-authorized:main", other["refusals"])
        _, risky = self.check(risk="critical", ok=False)
        self.assertIn("risk-above-ceiling:critical>medium", risky["refusals"])

    def test_an_unproven_certificate_refuses(self):
        self.grant()
        _, failed = self.check(cert=self.certificate(auditVerdict="FAIL"), ok=False)
        self.assertIn("certificate:audit-not-pass", failed["refusals"])
        _, red = self.check(cert=self.certificate(requiredCiAllSuccess=False), ok=False)
        self.assertIn("certificate:required-ci-not-green", red["refusals"])
        _, wrong = self.check(cert=self.certificate(workUnit="other"), ok=False)
        self.assertIn("certificate-work-unit-mismatch", wrong["refusals"])

    def test_a_project_hold_outranks_an_earlier_grant(self):
        self.grant()
        subprocess.run([sys.executable, str(GATE), "hold", "--project", "demo",
                        "--reason", "Owner pauses the project"], env=self.env,
                       check=True, capture_output=True)
        _, held = self.check(ok=False)
        self.assertTrue([r for r in held["refusals"] if r.startswith("gate-blocks:")])

    def test_a_work_unit_gate_blocks_only_its_own_work_unit(self):
        self.grant()
        subprocess.run([sys.executable, str(GATE), "create", "--project", "demo",
                        "--gate", "wu-1-question", "--work-unit", "wu-1",
                        "--question", "Ship this one?", "--choices", "YES,NO",
                        "--owner-only-category", "human-product-judgment-action",
                        "--scope", "work-unit"], env=self.env, check=True, capture_output=True)
        _, blocked = self.check(ok=False)
        self.assertIn("gate-blocks:wu-1-question", blocked["refusals"])
        cert = self.certificate(workUnit="wu-2")
        _, other = self.check(work_unit="wu-2", cert=cert)
        self.assertTrue(other["authorized"])

    def test_revocation_is_immediate(self):
        self.grant()
        self.cli("revoke", "--project", "demo", "--reason", "Owner takes merges back")
        _, out = self.check(ok=False)
        self.assertIn("authority-revoked", out["refusals"])

    def test_expiry_is_enforced(self):
        self.grant(**{"--ttl-seconds": "60"})
        path = self.runtime / "standing-authorities" / "demo" / "protected-merge.json"
        record = json.loads(path.read_text())
        record["expiresAt"] = self.now - 1000
        path.write_text(json.dumps(record))
        _, out = self.check(ok=False)
        self.assertIn("authority-expired", out["refusals"])

    def test_an_already_delivered_candidate_is_not_re_authorized(self):
        self.grant()
        self.git("update-ref", "refs/remotes/origin/dev", self.candidate)
        _, out = self.check(ok=False)
        self.assertIn("candidate-already-in-branch", out["refusals"])

    def test_a_candidate_absent_from_the_clone_refuses(self):
        self.grant()
        cert = self.certificate(candidateSha="0" * 40, auditedSha="0" * 40, mergeSha="0" * 40)
        _, out = self.check(cert=cert, ok=False)
        self.assertIn("candidate-absent-from-clone", out["refusals"])

    def test_use_writes_one_receipt_before_the_merge(self):
        self.grant()
        cert = self.certificate()
        _, first = self.cli("use", "--project", "demo", "--work-unit", "wu-1",
                            "--branch", "dev", "--certificate", str(cert),
                            "--repo", str(self.repo), "--session", "coord-1")
        self.assertTrue(first["authorized"])
        receipt = Path(first["receiptPath"])
        self.assertTrue(receipt.exists())
        self.assertEqual(json.loads(receipt.read_text())["workUnit"], "wu-1")
        # The same candidate may not be merged twice under one authority.
        cp, _ = self.cli("use", "--project", "demo", "--work-unit", "wu-1",
                         "--branch", "dev", "--certificate", str(cert),
                         "--repo", str(self.repo), ok=False)
        self.assertEqual(cp.returncode, 2)

    def test_use_refuses_without_authority_and_writes_nothing(self):
        cert = self.certificate()
        cp, out = self.cli("use", "--project", "demo", "--work-unit", "wu-1",
                           "--branch", "dev", "--certificate", str(cert),
                           "--repo", str(self.repo), ok=False)
        self.assertEqual(cp.returncode, 4)
        self.assertFalse(out["authorized"])
        self.assertFalse(list((self.runtime / "standing-merges").glob("*/*.json")))

    def test_only_direct_owner_may_grant(self):
        cp, _ = self.grant(ok=False, **{"--authority": "coordinator"})
        self.assertEqual(cp.returncode, 2)

    def test_list_reports_authorities_and_receipts(self):
        self.grant()
        self.cli("use", "--project", "demo", "--work-unit", "wu-1", "--branch", "dev",
                 "--certificate", str(self.certificate()), "--repo", str(self.repo))
        _, out = self.cli("list", "--project", "demo")
        self.assertEqual(len(out["authorities"]), 1)
        self.assertEqual(len(out["receipts"]), 1)


if __name__ == "__main__":
    unittest.main()

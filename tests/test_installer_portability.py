import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerPortabilityTest(unittest.TestCase):
    def test_special_characters_in_home_render_literally(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "a&b"
            home.mkdir()
            workspace = home / ".craft-agent/workspaces/test"
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["CRAFT_WORKSPACE"] = str(workspace)
            subprocess.run(
                [str(ROOT / "install.sh"), "--apply", "--workspace", str(workspace)],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
            service = home / ".config/systemd/user/craft-protocol-worker-watchdog.service"
            launchd = home / ".craft-agent/scripts/com.craft-protocol.worker-watchdog.plist"
            self.assertIn(f"ExecStart={home}/.craft-agent/scripts/watchdog-cron.sh", service.read_text())
            self.assertIn(f"{home}/.craft-agent/scripts/watchdog-cron.sh", launchd.read_text())
            self.assertNotIn("__HOME__", service.read_text())
            self.assertNotIn("__HOME__", launchd.read_text())


if __name__ == "__main__":
    unittest.main()

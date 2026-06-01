"""Smoke tests for the Unix installer manifest."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_INSTALL = _ROOT / "shim" / "install.sh"


class UnixInstallSmoke(unittest.TestCase):
    def test_install_copies_runtime_files_and_symlinks_agents(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "AGENT_MEMORY_HOME": home}
            result = subprocess.run([str(_INSTALL)], env=env, text=True,
                                    capture_output=True, timeout=20)

            self.assertEqual(result.returncode, 0, result.stderr)
            h = Path(home)
            for rel in (
                "bin/agent-shim", "bin/human-memory", "lib/watcher.py",
                "lib/memory_store.py", "lib/whiteboard.py", "lib/drafter.py",
                "lib/ui.py", "config.toml",
            ):
                self.assertTrue((h / rel).exists(), rel)
            for agent in ("claude", "codex", "opencode"):
                self.assertTrue((h / "bin" / agent).is_symlink(), agent)

    def test_reinstall_keeps_existing_config(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "AGENT_MEMORY_HOME": home}
            subprocess.run([str(_INSTALL)], env=env, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            config = Path(home) / "config.toml"
            config.write_text("# custom\n")

            result = subprocess.run([str(_INSTALL)], env=env, text=True,
                                    capture_output=True, timeout=20)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(), "# custom\n")
            self.assertIn("kept existing config", result.stdout)


if __name__ == "__main__":
    unittest.main()

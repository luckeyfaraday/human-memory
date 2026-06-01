"""Tests for the human-memory CLI wrapper."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CLI = _ROOT / "shim" / "human-memory"


class HumanMemoryCli(unittest.TestCase):
    def _run(self, args, *, cwd, home, input_text=None):
        env = {**os.environ, "AGENT_MEMORY_HOME": str(home)}
        return subprocess.run(
            [sys.executable, str(_CLI), *args],
            cwd=str(cwd), env=env, input=input_text, text=True,
            capture_output=True, timeout=10)

    def test_set_and_show_use_central_storage_by_default(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            result = self._run(["set", "agent-1"], cwd=Path(root), home=Path(home),
                               input_text="## Current State\ncentral body\n")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(root) / "HUMAN_MEMORY.md").exists())

            shown = self._run(["show"], cwd=Path(root), home=Path(home))

            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertIn("central body", shown.stdout)
            self.assertTrue(list((Path(home) / "projects").glob("*/HUMAN_MEMORY.md")))

    def test_show_reports_bad_storage_config_before_fallback(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            h = Path(home)
            h.mkdir(exist_ok=True)
            (h / "config.toml").write_text('[memory]\nstorage = "elsewhere"\n')
            result = self._run(["show"], cwd=Path(root), home=h)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bad memory.storage", result.stderr)


if __name__ == "__main__":
    unittest.main()

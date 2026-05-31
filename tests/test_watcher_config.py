"""Tests for watcher config parsing."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "shim"))
_SPEC = importlib.util.spec_from_file_location("watcher", _ROOT / "shim" / "watcher.py")
watcher = importlib.util.module_from_spec(_SPEC)
sys.modules["watcher"] = watcher
_SPEC.loader.exec_module(watcher)


class WatcherConfig(unittest.TestCase):
    def test_drafter_command_override_is_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text('[drafter]\ncommand = ["fake-agent", "{prompt}"]\n')

            cfg, note = watcher.load_config(p)

        self.assertEqual(cfg.draft_command, ("fake-agent", "{prompt}"))
        self.assertIn("draft_command", note)

    def test_bad_drafter_command_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text('[drafter]\ncommand = "fake-agent {prompt}"\n')

            cfg, note = watcher.load_config(p)

        self.assertIsNone(cfg.draft_command)
        self.assertIn("bad values", note)


if __name__ == "__main__":
    unittest.main()

"""Tests for the local browser dashboard helpers."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent

MEMORY_SPEC = importlib.util.spec_from_file_location("memory_store", ROOT / "shim" / "memory_store.py")
memory_store = importlib.util.module_from_spec(MEMORY_SPEC)
sys.modules["memory_store"] = memory_store
MEMORY_SPEC.loader.exec_module(memory_store)

UI_SPEC = importlib.util.spec_from_file_location("ui", ROOT / "shim" / "ui.py")
ui = importlib.util.module_from_spec(UI_SPEC)
UI_SPEC.loader.exec_module(ui)


class DashboardHelpers(unittest.TestCase):
    def test_known_projects_combines_metadata_and_live_sessions(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"AGENT_MEMORY_HOME": home}):
                location = memory_store.resolve(Path(root), "central")
                memory_store.ensure_location(location)
                sessions = [{
                    "agent": "codex",
                    "pid": 123,
                    "cwd": str(Path(root).resolve()),
                    "stale": True,
                    "memory_path": str(location.path),
                    "memory_storage": "central",
                    "project_id": location.project_id,
                }]

                projects = ui.known_projects(memory_store, sessions)

        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_id"], location.project_id)
        self.assertEqual(projects[0]["live_sessions"], 1)
        self.assertEqual(projects[0]["stale_sessions"], 1)
        self.assertEqual(projects[0]["memory_path"], str(location.path))

    def test_known_projects_skips_bad_metadata(self):
        with tempfile.TemporaryDirectory() as home:
            projects_dir = Path(home) / "projects" / "bad"
            projects_dir.mkdir(parents=True)
            (projects_dir / "metadata.json").write_text("{not json")
            with patch.dict(os.environ, {"AGENT_MEMORY_HOME": home}):
                projects = ui.known_projects(memory_store, [])

        self.assertEqual(projects, [])

    def test_read_memory_reports_text_and_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "HUMAN_MEMORY.md"
            p.write_text("# HUMAN_MEMORY.md\n")

            found = ui.read_memory(str(p))
            missing = ui.read_memory(str(Path(d) / "missing.md"))

        self.assertTrue(found["ok"])
        self.assertEqual(found["text"], "# HUMAN_MEMORY.md\n")
        self.assertFalse(missing["ok"])
        self.assertIn("error", missing)


if __name__ == "__main__":
    unittest.main()

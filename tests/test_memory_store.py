"""Tests for central/project-file memory path resolution."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location(
    "memory_store", Path(__file__).resolve().parent.parent / "shim" / "memory_store.py")
memory_store = importlib.util.module_from_spec(_SPEC)
import sys
sys.modules["memory_store"] = memory_store
_SPEC.loader.exec_module(memory_store)


class MemoryStore(unittest.TestCase):
    def test_central_storage_uses_agent_memory_projects(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"AGENT_MEMORY_HOME": home}):
                location = memory_store.resolve(Path(root), "central")

                self.assertEqual(location.storage, "central")
                self.assertTrue(str(location.path).startswith(str(Path(home) / "projects")))
                self.assertEqual(location.path.name, "HUMAN_MEMORY.md")
                self.assertEqual(location.metadata_path.name, "metadata.json")

    def test_project_file_storage_uses_working_tree(self):
        with tempfile.TemporaryDirectory() as root:
            location = memory_store.resolve(Path(root), "project-file")

            self.assertEqual(location.path, Path(root).resolve() / "HUMAN_MEMORY.md")
            self.assertIsNone(location.metadata_path)

    def test_ensure_location_writes_metadata_for_central_storage(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"AGENT_MEMORY_HOME": home}):
                location = memory_store.resolve(Path(root), "central")
                memory_store.ensure_location(location)

                self.assertTrue(location.path.parent.exists())
                text = location.metadata_path.read_text()
                self.assertIn(str(Path(root).resolve()), text)
                self.assertIn(str(location.path), text)

    def test_load_storage_defaults_to_central(self):
        with tempfile.TemporaryDirectory() as d:
            storage, note = memory_store.load_storage(Path(d) / "missing.toml")

        self.assertEqual(storage, "central")
        self.assertIn("using central", note)


if __name__ == "__main__":
    unittest.main()

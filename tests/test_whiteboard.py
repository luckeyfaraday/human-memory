"""Tests for shim/whiteboard.py — the concurrency-safe per-agent updater.

Run: python3 -m unittest tests.test_whiteboard   (from repo root)
stdlib only; no pytest dependency.
"""

import importlib.util
import multiprocessing
import tempfile
import unittest
from pathlib import Path

# whiteboard.py has no .py-importable package; load it by path.
_SPEC = importlib.util.spec_from_file_location(
    "whiteboard", Path(__file__).resolve().parent.parent / "shim" / "whiteboard.py")
wb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wb)


class ReplaceAgentBlock(unittest.TestCase):
    def test_append_then_replace_is_idempotent_and_localized(self):
        c0 = ""
        c1 = wb.replace_agent_block(c0, "claude", "Current State: A")
        self.assertIn("<!-- hm:agent=claude -->", c1)
        self.assertIn("Current State: A", c1)
        # Replacing claude's block again leaves a single block (no duplication).
        c2 = wb.replace_agent_block(c1, "claude", "Current State: B")
        self.assertEqual(c2.count("<!-- hm:agent=claude -->"), 1)
        self.assertIn("Current State: B", c2)
        self.assertNotIn("Current State: A", c2)
        # Same body again → byte-identical (idempotent).
        self.assertEqual(c2, wb.replace_agent_block(c2, "claude", "Current State: B"))

    def test_unfenced_human_text_is_sacred(self):
        human = "# HUMAN_MEMORY.md\n\nhand-written notes the agent must not eat\n"
        out = wb.replace_agent_block(human, "codex", "codex section")
        self.assertIn("hand-written notes the agent must not eat", out)
        self.assertIn("<!-- hm:agent=codex -->", out)

    def test_two_agents_coexist_and_dont_touch_each_other(self):
        c = wb.replace_agent_block("", "claude", "claude work")
        c = wb.replace_agent_block(c, "codex", "codex work")
        self.assertIn("claude work", c)
        self.assertIn("codex work", c)
        # Editing codex leaves claude's bytes untouched.
        c2 = wb.replace_agent_block(c, "codex", "codex work v2")
        self.assertIn("claude work", c2)
        self.assertIn("codex work v2", c2)

    def test_remove_block(self):
        c = wb.replace_agent_block("", "claude", "x")
        c = wb.replace_agent_block(c, "codex", "y")
        c = wb.replace_agent_block(c, "claude", None)
        self.assertNotIn("hm:agent=claude", c)
        self.assertIn("hm:agent=codex", c)

    def test_unsafe_agent_name_rejected(self):
        with self.assertRaises(ValueError):
            wb.replace_agent_block("", "../evil", "x")


def _worker(args):
    path, agent, n = args
    for i in range(n):
        wb.update_file(Path(path), agent, f"{agent} iter {i}")


class ConcurrentWriters(unittest.TestCase):
    def test_disjoint_agents_no_lost_updates(self):
        # N processes, each hammering its own agent block simultaneously.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "HUMAN_MEMORY.md"
            agents = [f"agent{i}" for i in range(6)]
            with multiprocessing.Pool(len(agents)) as pool:
                pool.map(_worker, [(str(path), a, 25) for a in agents])
            final = path.read_text()
            # Every agent's block must survive exactly once with its last value.
            for a in agents:
                self.assertEqual(final.count(f"<!-- hm:agent={a} -->"), 1,
                                 f"{a} block missing or duplicated")
                self.assertIn(f"{a} iter 24", final)

    def test_same_agent_concurrent_no_corruption(self):
        # Many writers, same agent: last-writer-wins, but never a torn/dup block.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "HUMAN_MEMORY.md"
            with multiprocessing.Pool(8) as pool:
                pool.map(_worker, [(str(path), "claude", 15) for _ in range(8)])
            final = path.read_text()
            self.assertEqual(final.count("<!-- hm:agent=claude -->"), 1)
            self.assertEqual(final.count("<!-- /hm:agent=claude -->"), 1)


if __name__ == "__main__":
    unittest.main()

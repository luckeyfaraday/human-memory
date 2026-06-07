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
    def test_drafter_throttle_defaults_are_conservative(self):
        cfg = watcher.Config()

        self.assertEqual(cfg.memory_storage, "central")
        self.assertEqual(cfg.draft_quiescence_seconds, 300)
        self.assertEqual(cfg.draft_min_edit_ticks, 6)
        self.assertEqual(cfg.draft_min_diff_chars, 200)
        self.assertEqual(cfg.draft_max_drafts_per_session, 2)
        self.assertFalse(cfg.draft_always_on_exit)

    def test_memory_storage_config_is_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text('[memory]\nstorage = "project-file"\n')

            cfg, note = watcher.load_config(p)

        self.assertEqual(cfg.memory_storage, "project-file")
        self.assertIn("memory_storage", note)

    def test_bad_memory_storage_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text('[memory]\nstorage = "somewhere-else"\n')

            cfg, note = watcher.load_config(p)

        self.assertEqual(cfg.memory_storage, "central")
        self.assertIn("bad values", note)

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

    def test_drafter_throttle_config_is_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(
                "[drafter]\n"
                "quiescence_seconds = 90\n"
                "min_edit_ticks = 5\n"
                "min_diff_chars = 400\n"
                "max_drafts_per_session = 1\n"
                "always_on_exit = false\n"
            )

            cfg, note = watcher.load_config(p)

        self.assertEqual(cfg.draft_quiescence_seconds, 90)
        self.assertEqual(cfg.draft_min_edit_ticks, 5)
        self.assertEqual(cfg.draft_min_diff_chars, 400)
        self.assertEqual(cfg.draft_max_drafts_per_session, 1)
        self.assertFalse(cfg.draft_always_on_exit)
        self.assertIn("draft_min_edit_ticks", note)

    def test_bad_drafter_throttle_values_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text("[drafter]\nmin_edit_ticks = 0\n")

            cfg, note = watcher.load_config(p)

        self.assertEqual(cfg.draft_min_edit_ticks, watcher.Config().draft_min_edit_ticks)
        self.assertIn("bad values", note)

    def test_quoted_boolean_values_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(
                '[drafter]\n'
                'enabled = "false"\n'
                'include_git_diff = "false"\n'
                'always_on_exit = "false"\n'
            )

            cfg, note = watcher.load_config(p)

        defaults = watcher.Config()
        self.assertEqual(cfg.draft_enabled, defaults.draft_enabled)
        self.assertEqual(cfg.draft_include_git_diff, defaults.draft_include_git_diff)
        self.assertEqual(cfg.draft_always_on_exit, defaults.draft_always_on_exit)
        self.assertIn("must be a TOML boolean", note)


class DraftGates(unittest.TestCase):
    def test_quiescence_requires_min_edits_and_budget(self):
        cfg = watcher.Config(draft_quiescence_seconds=120, draft_min_edit_ticks=3,
                             draft_max_drafts_per_session=2, draft_min_diff_chars=0)

        self.assertFalse(watcher.should_draft_quiescence(
            drafting=True, unrecorded_edits=2, last_edit_at=0,
            last_seen_newest=10, last_drafted_newest=9, draft_count=0,
            diff_chars=500, cfg=cfg, now_value=130))
        self.assertFalse(watcher.should_draft_quiescence(
            drafting=True, unrecorded_edits=3, last_edit_at=0,
            last_seen_newest=10, last_drafted_newest=9, draft_count=2,
            diff_chars=500, cfg=cfg, now_value=130))
        self.assertTrue(watcher.should_draft_quiescence(
            drafting=True, unrecorded_edits=3, last_edit_at=0,
            last_seen_newest=10, last_drafted_newest=9, draft_count=1,
            diff_chars=500, cfg=cfg, now_value=130))

    def test_exit_draft_is_separate_from_mid_session_budget(self):
        cfg = watcher.Config(draft_max_drafts_per_session=0, draft_always_on_exit=True,
                             draft_min_diff_chars=0)

        self.assertTrue(watcher.should_draft_exit(
            drafting=True, unrecorded_edits=1, last_seen_newest=10,
            last_drafted_newest=9, diff_chars=500, cfg=cfg))
        self.assertFalse(watcher.should_draft_exit(
            drafting=True, unrecorded_edits=1, last_seen_newest=10,
            last_drafted_newest=9, diff_chars=500,
            cfg=watcher.Config(draft_always_on_exit=False)))

    def test_min_diff_chars_gates_quiescence(self):
        cfg = watcher.Config(draft_quiescence_seconds=0, draft_min_edit_ticks=1,
                             draft_max_drafts_per_session=99, draft_min_diff_chars=500)

        # diff below threshold → skip the LLM call
        self.assertFalse(watcher.should_draft_quiescence(
            drafting=True, unrecorded_edits=10, last_edit_at=0,
            last_seen_newest=10, last_drafted_newest=9, draft_count=0,
            diff_chars=499, cfg=cfg, now_value=10))
        # diff at or above threshold → fire
        self.assertTrue(watcher.should_draft_quiescence(
            drafting=True, unrecorded_edits=10, last_edit_at=0,
            last_seen_newest=10, last_drafted_newest=9, draft_count=0,
            diff_chars=500, cfg=cfg, now_value=10))

    def test_min_diff_chars_gates_exit(self):
        cfg = watcher.Config(draft_always_on_exit=True, draft_min_diff_chars=100)

        self.assertFalse(watcher.should_draft_exit(
            drafting=True, unrecorded_edits=5, last_seen_newest=10,
            last_drafted_newest=9, diff_chars=99, cfg=cfg))
        self.assertTrue(watcher.should_draft_exit(
            drafting=True, unrecorded_edits=5, last_seen_newest=10,
            last_drafted_newest=9, diff_chars=100, cfg=cfg))


class BootstrapMemory(unittest.TestCase):
    def test_bootstrap_missing_memory_creates_agent_block(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)

            created = watcher.bootstrap_missing_memory(
                root, "codex", "codex-123", include_git_diff=True)

            p = root / "HUMAN_MEMORY.md"
            self.assertTrue(created)
            self.assertTrue(p.exists())
            text = p.read_text()
            self.assertIn("<!-- hm:session=codex-123 -->", text)
            self.assertIn("skeleton only", text)

    def test_bootstrap_missing_memory_leaves_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / "HUMAN_MEMORY.md"
            p.write_text("# human notes\n")

            created = watcher.bootstrap_missing_memory(
                root, "codex", "codex-123", include_git_diff=True)

            self.assertFalse(created)
            self.assertEqual(p.read_text(), "# human notes\n")

    def test_session_owner_includes_pid(self):
        self.assertEqual(watcher.session_owner("codex", 123), "codex-123")


if __name__ == "__main__":
    unittest.main()

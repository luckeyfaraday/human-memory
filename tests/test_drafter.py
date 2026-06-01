"""Tests for shim/drafter.py — the hybrid whiteboard author.

Run: python3 tests/test_drafter.py      (from repo root)
stdlib only. The model call is exercised with a fake "agent" script, so no
network/tokens are needed.
"""

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "drafter", Path(__file__).resolve().parent.parent / "shim" / "drafter.py")
dr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dr)


def _git(d, *a):
    subprocess.run(["git", "-C", str(d), *a], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _repo_with_change():
    d = Path(tempfile.mkdtemp())
    _git(d, "init")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / "a.py").write_text("x = 1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-m", "init")
    (d / "a.py").write_text("x = 2\ndef f():\n    pass\n# TODO: wire it up\n")
    return d


def _fake_bin(script: str) -> str:
    """Write an executable shell script and return its path."""
    fd, path = tempfile.mkstemp(suffix=".sh")
    os.write(fd, ("#!/usr/bin/env bash\n" + script).encode())
    os.close(fd)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


class CollectAndSkeleton(unittest.TestCase):
    def test_collect_changes_from_git(self):
        d = _repo_with_change()
        info = dr.collect_changes(d)
        self.assertIn("a.py", info["changed_files"])
        self.assertEqual(info["newest_file"], "a.py")
        self.assertTrue(any("TODO" in t for t in info["todos"]))
        self.assertIn("def f", info["diff"])

    def test_collect_captures_untracked_files_without_reading_todos(self):
        # A brand-new file is untracked; list its name, but do not read its
        # contents into the prompt because it may be private scratch data.
        d = _repo_with_change()
        (d / "brand_new.py").write_text("# TODO: secret scratch context\n")
        info = dr.collect_changes(d)
        self.assertIn("brand_new.py", info["changed_files"])
        self.assertFalse(any("secret scratch" in t for t in info["todos"]))

    def test_collect_handles_spaces_and_arrows_in_paths(self):
        d = _repo_with_change()
        (d / "arrow -> name.txt").write_text("new\n")
        (d / "space name.txt").write_text("new\n")
        info = dr.collect_changes(d)
        self.assertIn("arrow -> name.txt", info["changed_files"])
        self.assertIn("space name.txt", info["changed_files"])

    def test_collect_ignores_memory_artifacts(self):
        d = _repo_with_change()
        (d / "HUMAN_MEMORY.md").write_text("# stale handoff\nTODO: noisy\n")
        (d / "HUMAN_MEMORY.md.bak").write_text("# backup\nTODO: also noisy\n")
        (d / ".agent-memory").mkdir()
        (d / ".agent-memory" / "scratch.txt").write_text("TODO: central noise\n")

        info = dr.collect_changes(d)

        self.assertIn("a.py", info["changed_files"])
        self.assertNotIn("HUMAN_MEMORY.md", info["changed_files"])
        self.assertNotIn("HUMAN_MEMORY.md.bak", info["changed_files"])
        self.assertFalse(any("HUMAN_MEMORY.md" in t for t in info["todos"]))

    def test_diff_ignores_tracked_memory_artifacts(self):
        d = Path(tempfile.mkdtemp())
        _git(d, "init")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
        (d / "HUMAN_MEMORY.md").write_text("# initial\n")
        (d / "real.py").write_text("x = 1\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-m", "init")
        (d / "HUMAN_MEMORY.md").write_text("# changed memory\n")
        (d / "real.py").write_text("x = 2\n")

        info = dr.collect_changes(d)

        self.assertEqual(info["changed_files"], ["real.py"])
        self.assertIn("real.py", info["diff"])
        self.assertNotIn("HUMAN_MEMORY.md", info["diff"])

    def test_diff_ignores_memory_artifacts_with_spaces(self):
        d = Path(tempfile.mkdtemp())
        _git(d, "init")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
        (d / "foo bar").mkdir()
        (d / "foo bar" / "HUMAN_MEMORY.md").write_text("# initial\n")
        (d / "real.py").write_text("x = 1\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-m", "init")
        (d / "foo bar" / "HUMAN_MEMORY.md").write_text("# changed memory\n")
        (d / "real.py").write_text("x = 2\n")

        info = dr.collect_changes(d)

        self.assertIn("real.py", info["diff"])
        self.assertNotIn("foo bar/HUMAN_MEMORY.md", info["diff"])

    def test_diff_ignores_renames_from_memory_artifacts(self):
        d = Path(tempfile.mkdtemp())
        _git(d, "init")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
        (d / "HUMAN_MEMORY.md").write_text("# initial\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-m", "init")
        _git(d, "mv", "HUMAN_MEMORY.md", "notes.md")

        info = dr.collect_changes(d)

        self.assertNotIn("HUMAN_MEMORY.md", info["diff"])
        self.assertNotIn("notes.md", info["diff"])

    def test_diff_ignores_mode_only_memory_artifact_sections(self):
        diff = "diff --git a/HUMAN_MEMORY.md b/HUMAN_MEMORY.md\nold mode 100644\nnew mode 100755\n"

        self.assertEqual(dr.filter_memory_artifact_diff(diff), "")

    def test_skeleton_has_five_sections(self):
        d = _repo_with_change()
        sk = dr.build_skeleton(dr.collect_changes(d))
        for h in ("## Current State", "## What Just Happened", "## Pending",
                  "## Key Decisions", "## Where I Left Off"):
            self.assertIn(h, sk)
        self.assertIn("a.py", sk)

    def test_prompt_warns_against_confabulating_decisions(self):
        d = _repo_with_change()
        info = dr.collect_changes(d)
        p = dr.compose_prompt("prev block", info, dr.build_skeleton(info))
        self.assertIn("do NOT invent", p)
        self.assertIn("git diff", p)


class RunAgent(unittest.TestCase):
    def test_success_returns_stdout(self):
        b = _fake_bin('echo "## Current State\nhi"')
        out = dr.run_agent(b, "claude", "prompt", "haiku", 5,
                           command=[b])  # ignore args, just echo
        self.assertIn("Current State", out)

    def test_sets_internal_env_for_n1(self):
        b = _fake_bin('echo "internal=$AGENT_MEMORY_INTERNAL"')
        out = dr.run_agent(b, "claude", "p", "haiku", 5, command=[b])
        self.assertEqual(out, "internal=1")

    def test_nonzero_exit_returns_none(self):
        b = _fake_bin("exit 3")
        self.assertIsNone(dr.run_agent(b, "claude", "p", "haiku", 5, command=[b]))

    def test_empty_output_returns_none(self):
        b = _fake_bin("true")
        self.assertIsNone(dr.run_agent(b, "claude", "p", "haiku", 5, command=[b]))

    def test_timeout_returns_none(self):
        b = _fake_bin("sleep 5")
        self.assertIsNone(dr.run_agent(b, "claude", "p", "haiku", 0.3, command=[b]))

    def test_outfile_read_mode(self):
        # codex writes its answer to a file (-o), not stdout. Simulate that agent's
        # spec: a fake bin that writes its last arg's target and prints chrome to
        # stdout, proving we read the FILE and ignore stdout.
        b = _fake_bin('echo "noisy chrome on stdout"; printf "FILE ANSWER" > "$2"')
        orig = dr.DEFAULT_COMMANDS.get("faux")
        dr.DEFAULT_COMMANDS["faux"] = {
            "argv": [b, "-o", "{outfile}", "{prompt}"], "read": "outfile", "use_model": False}
        try:
            out = dr.run_agent(b, "faux", "summarize", "haiku", 5)
        finally:
            dr.DEFAULT_COMMANDS.pop("faux", None)
            if orig is not None:
                dr.DEFAULT_COMMANDS["faux"] = orig
        self.assertEqual(out, "FILE ANSWER")  # file content, not the stdout chrome

    def test_custom_command_outfile_placeholder_is_available(self):
        b = _fake_bin('printf "CUSTOM FILE ANSWER" > "$1"')
        out = dr.run_agent(b, "claude", "p", "haiku", 5, command=[b, "{outfile}"])
        self.assertEqual(out, "CUSTOM FILE ANSWER")

    def test_parse_opencode_json(self):
        jsonl = (
            '{"type":"step_start","part":{"type":"step-start"}}\n'
            '{"type":"text","part":{"type":"text","text":"Hello "}}\n'
            '{"type":"text","part":{"type":"text","text":"world"}}\n'
            'not json — ignored\n'
        )
        self.assertEqual(dr.parse_opencode_json(jsonl), "Hello world")
        self.assertIsNone(dr.parse_opencode_json(""))
        self.assertIsNone(dr.parse_opencode_json('{"type":"step_start"}\n'))

    def test_real_specs_have_required_keys(self):
        for agent, spec in dr.DEFAULT_COMMANDS.items():
            self.assertIn(spec["read"], ("stdout", "outfile", "opencode_json"), agent)
            self.assertIn("{prompt}", spec["argv"], agent)
            if spec["read"] == "outfile":
                self.assertIn("{outfile}", spec["argv"], agent)

    def test_codex_uses_codex_drafter_model(self):
        spec = dr.DEFAULT_COMMANDS["codex"]
        self.assertEqual(spec["agent_model"], "gpt-5.4-mini")
        self.assertIn("--model", spec["argv"])
        self.assertIn("{agent_model}", spec["argv"])


class DraftBlock(unittest.TestCase):
    def test_no_real_bin_falls_back_to_skeleton(self):
        d = _repo_with_change()
        body = dr.draft_block(d, "claude", real_bin=None, model="haiku", timeout=5,
                              prev_block=None, include_git_diff=True)
        self.assertIn("skeleton only", body)
        self.assertIn("a.py", body)

    def test_uses_model_output_when_available(self):
        d = _repo_with_change()
        b = _fake_bin('echo "## Current State\nMODEL SAYS HI"')
        body = dr.draft_block(d, "claude", real_bin=b, model="haiku", timeout=5,
                              prev_block=None, include_git_diff=True, command=[b])
        self.assertIn("MODEL SAYS HI", body)

    def test_model_failure_falls_back_to_skeleton(self):
        d = _repo_with_change()
        b = _fake_bin("exit 1")
        body = dr.draft_block(d, "claude", real_bin=b, model="haiku", timeout=5,
                              prev_block=None, include_git_diff=True, command=[b])
        self.assertIn("skeleton only", body)


if __name__ == "__main__":
    unittest.main()

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

    def test_collect_captures_untracked_files(self):
        # A brand-new file is untracked; `git diff HEAD` would miss it but
        # `git status --porcelain` catches it — the common "agent made a file" case.
        d = _repo_with_change()
        (d / "brand_new.py").write_text("print('hi')\n")
        info = dr.collect_changes(d)
        self.assertIn("brand_new.py", info["changed_files"])

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

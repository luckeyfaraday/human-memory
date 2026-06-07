#!/usr/bin/env python3
"""drafter.py — author a HUMAN_MEMORY.md block from observed changes.

Hybrid by design (see docs/llm-drafter-design.md):

  * The deterministic skeleton (git diff / changed files / TODOs) needs no model,
    no tokens, no network — it always works and is the floor.
  * If a model is available, it polishes the skeleton into the five sections.
    The model runs as the user's OWN already-authed agent in headless mode, at a
    cheap model, called via the real binary with AGENT_MEMORY_INTERNAL=1 so it
    never re-enters the shim (N1). Any failure/timeout falls back to the skeleton.

Honesty constraint baked into the prompt: the *why* behind a decision usually
isn't in a diff, so the model is told to omit decisions it can't infer with
confidence rather than invent them.

This is the body of `draft_update()`. Best-effort throughout: it must never hang
or break the agent session. stdlib + git only.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

MEMORY_FILE = "HUMAN_MEMORY.md"
MAX_DIFF_CHARS = 12000  # bound the model input (and our token spend)

# Headless invocation per agent. Placeholders: {bin} {prompt} {model} {outfile}.
# `read` says where the agent's answer lands: "stdout" or "outfile" (a temp file
# whose path is substituted for {outfile}). `{model}` is the shared
# [drafter].model (a Claude name like "haiku"); `{agent_model}` is an agent-
# specific drafter model where the shared value does not apply.
#
# All three verified by real `Reply with PONG` runs (2026-05-31):
#   claude   → clean stdout
#   codex    → stdout is chrome; -o writes the clean answer to a file; also needs
#              stdin=DEVNULL (see run_agent) or it blocks reading stdin; uses
#              gpt-5.4-mini as the cheap drafter model
#   opencode → its `run` default format prints to a TTY/file but emits NOTHING to a
#              pipe (which is what we capture), so we use `--format json` and parse
#              the assistant text from the JSONL events; -m needs provider/model so
#              we omit it and let opencode use its configured default
DEFAULT_COMMANDS = {
    "claude": {
        "argv": ["{bin}", "-p", "{prompt}", "--model", "{model}"],
        "read": "stdout", "use_model": True,
    },
    "codex": {
        "argv": ["{bin}", "exec", "--sandbox", "read-only", "--skip-git-repo-check",
                 "--model", "{agent_model}", "-o", "{outfile}", "{prompt}"],
        "read": "outfile", "use_model": False, "agent_model": "gpt-5.4-mini",
    },
    "opencode": {
        "argv": ["{bin}", "run", "--format", "json", "{prompt}"],
        "read": "opencode_json", "use_model": False,
    },
}


def parse_opencode_json(stdout: str) -> str | None:
    """Concatenate the assistant text from opencode's `--format json` JSONL."""
    import json
    parts = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "text":
            text = ev.get("part", {}).get("text")
            if text:
                parts.append(text)
    return "".join(parts).strip() or None


def _git(root: Path, *args: str, timeout: float = 5) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _git_bytes(root: Path, *args: str, timeout: float = 5) -> bytes | None:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def is_git_repo(root: Path) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree") is not None


def is_memory_artifact(path: str | Path) -> bool:
    """Return whether a project-relative path is human-memory's own state."""
    parts = Path(path).parts
    if ".agent-memory" in parts:
        return True
    name = parts[-1] if parts else str(path)
    return name == MEMORY_FILE or name.startswith(f"{MEMORY_FILE}.")


def filter_memory_artifacts(paths: list[str]) -> list[str]:
    return [p for p in paths if not is_memory_artifact(p)]


def _diff_path_token(path: str) -> str | None:
    path = path.strip().split("\t", 1)[0]
    if path == "/dev/null":
        return None
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path or None


def _diff_header_paths(line: str) -> list[str]:
    if not line.startswith("diff --git "):
        return []
    try:
        parts = shlex.split(line.strip())
    except ValueError:
        parts = line.strip().split()
    paths = []
    for token in parts[2:4]:
        path = _diff_path_token(token)
        if path:
            paths.append(path)
    return paths


def _diff_section_paths(section: list[str]) -> list[str]:
    """Return project-relative paths named by a unified diff section."""
    paths = []
    for line in section:
        if line.startswith("diff --git "):
            paths.extend(_diff_header_paths(line))
        for prefix in ("--- ", "+++ "):
            if line.startswith(prefix):
                path = _diff_path_token(line[len(prefix):])
                if path:
                    paths.append(path)
        if line.startswith("rename from ") or line.startswith("rename to "):
            paths.append(line.split(" ", 2)[2].strip())
    return paths


def filter_memory_artifact_diff(diff: str) -> str:
    """Drop unified-diff sections for human-memory's own files."""
    sections: list[list[str]] = []
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current:
                sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)

    kept: list[str] = []
    for section in sections:
        paths = _diff_section_paths(section)
        if paths and any(is_memory_artifact(path) for path in paths):
            continue
        kept.extend(section)
    return "".join(kept)


def _parse_status_z(raw: bytes) -> tuple[list[str], set[str]]:
    """Parse `git status --porcelain=v1 -z` into changed and tracked-content paths."""
    changed: list[str] = []
    content_safe: set[str] = set()
    records = [r for r in raw.decode("utf-8", errors="surrogateescape").split("\0") if r]
    i = 0
    while i < len(records):
        record = records[i]
        if len(record) < 4:
            i += 1
            continue
        status = record[:2]
        path = record[3:]
        if status.startswith("R") or status.startswith("C"):
            if i + 1 < len(records):
                old_path = records[i + 1]
                changed.append(path)
                if not status.startswith("??") and not status.startswith("!!"):
                    content_safe.add(path)
                    content_safe.add(old_path)
                i += 2
                continue
        changed.append(path)
        if not status.startswith("??") and not status.startswith("!!"):
            content_safe.add(path)
        i += 1
    return changed, content_safe


def collect_changes(root: Path, include_git_diff: bool = True) -> dict:
    """Gather the deterministic facts a draft is built from.

    Returns {changed_files, newest_file, todos, diff}. Prefers git; falls back to
    an mtime walk when not a repo. Untracked filenames are listed, but their
    contents are not read into TODOs by default because drafts may be sent to a
    model provider.
    """
    info: dict = {"changed_files": [], "newest_file": None, "todos": [], "diff": ""}
    todo_paths: set[str] | None = None

    if include_git_diff and is_git_repo(root):
        # NUL-delimited porcelain keeps spaces, quotes, arrows, and unusual bytes
        # unambiguous. Untracked files are listed as changed, but only tracked
        # files are read for TODO/FIXME content to avoid leaking scratch secrets.
        status = _git_bytes(root, "status", "--porcelain=v1", "-z") or b""
        files, todo_paths = _parse_status_z(status)
        info["changed_files"] = filter_memory_artifacts(files)
        todo_paths = set(filter_memory_artifacts(sorted(todo_paths)))
        diff = _git(root, "diff", "HEAD") or ""  # tracked changes (untracked listed above)
        info["diff"] = filter_memory_artifact_diff(diff)[:MAX_DIFF_CHARS]
    else:
        # No git: newest few files by mtime, excluding human-memory's own state.
        files = []
        for dp, dirs, fns in os.walk(root):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in {"node_modules", ".agent-memory"}
            ]
            for fn in fns:
                p = Path(dp) / fn
                rel = p.relative_to(root)
                if is_memory_artifact(rel):
                    continue
                try:
                    files.append((p.stat().st_mtime, p))
                except OSError:
                    pass
        files.sort(reverse=True)
        info["changed_files"] = [str(p.relative_to(root)) for _, p in files[:10]]

    # Newest changed file (for "Where I Left Off").
    newest_mtime = -1.0
    for rel in info["changed_files"]:
        p = root / rel
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest_mtime:
            newest_mtime, info["newest_file"] = m, rel

    # TODO/FIXME in changed files (cheap pending-work signal). In git repos,
    # restrict content reads to tracked files; untracked filenames can be useful
    # context, but their contents may be private scratch data.
    for rel in info["changed_files"][:20]:
        if todo_paths is not None and rel not in todo_paths:
            continue
        p = root / rel
        try:
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                if "TODO" in line or "FIXME" in line:
                    info["todos"].append(f"{rel}:{i}: {line.strip()[:80]}")
                    if len(info["todos"]) >= 10:
                        break
        except OSError:
            pass
    return info


def build_skeleton(info: dict) -> str:
    """A useful five-section block with zero model calls."""
    files = info["changed_files"]
    what = "\n".join(f"- changed `{f}`" for f in files[:8]) or "- (no tracked changes detected)"
    pending = "\n".join(f"- [ ] {t}" for t in info["todos"]) or "- _(no TODO/FIXME markers found in changed files)_"
    left = f"`{info['newest_file']}` — most recently modified." if info["newest_file"] else "_(unknown)_"
    return (
        "## Current State\n_(auto-draft: model unavailable — skeleton only)_\n\n"
        "## What Just Happened\n" + what + "\n\n"
        "## Pending\n" + pending + "\n\n"
        "## Key Decisions\n_(not inferable from changes alone)_\n\n"
        "## Where I Left Off\n" + left + "\n"
    )


def compose_prompt(prev_block: str | None, info: dict, skeleton: str) -> str:
    diff = info["diff"] or "(no diff available)"
    prev = prev_block.strip() if prev_block else "(none yet)"
    return (
        "You maintain HUMAN_MEMORY.md, a running whiteboard that lets a human "
        "reload context on this work in ~10 seconds. Update it from the recent "
        "changes below.\n\n"
        "Output ONLY markdown with exactly these five sections, in order:\n"
        "## Current State\n## What Just Happened\n## Pending\n## Key Decisions\n"
        "## Where I Left Off\n\n"
        "Be terse and concrete. Prefer file/line references. IMPORTANT: for "
        "'Key Decisions', include a decision ONLY if the reason is clearly "
        "evident from the changes — do NOT invent rationales; omit if unsure.\n\n"
        f"--- Previous whiteboard block ---\n{prev}\n\n"
        f"--- Deterministic skeleton (facts) ---\n{skeleton}\n\n"
        f"--- git diff (truncated) ---\n{diff}\n"
    )


def run_agent(real_bin: str, agent: str, prompt: str, model: str,
              timeout: float, command: list[str] | None = None) -> str | None:
    """Invoke the user's own agent headlessly; return its answer or None on failure.

    `command`, if given, is a raw argv list read from stdout (used by tests and the
    [drafter].command override). Otherwise the per-agent spec in DEFAULT_COMMANDS
    decides the argv, whether the model flag applies, and whether the answer comes
    from stdout or a temp output file (codex).
    """
    if command is not None:
        spec = {"argv": command, "read": "stdout", "use_model": True}
    else:
        spec = DEFAULT_COMMANDS.get(agent)
        if not spec:
            return None

    outfile = None
    fields = {
        "bin": real_bin,
        "prompt": prompt,
        "model": model,
        "agent_model": str(spec.get("agent_model") or model),
    }
    needs_outfile = spec["read"] == "outfile" or any("{outfile}" in part for part in spec["argv"])
    if needs_outfile:
        fd, outfile = tempfile.mkstemp(prefix="hm-draft-", suffix=".md")
        os.close(fd)
        fields["outfile"] = outfile

    try:
        argv = [part.format(**fields) for part in spec["argv"]]
    except KeyError:
        if outfile:
            try:
                os.unlink(outfile)
            except OSError:
                pass
        return None
    env = {**os.environ, "AGENT_MEMORY_INTERNAL": "1"}  # N1: never re-spawn a watcher
    try:
        # stdin=DEVNULL is REQUIRED: `codex exec` reads stdin for extra context
        # and blocks until EOF when stdin isn't a TTY — without this it hangs
        # until the timeout. Harmless for the others.
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        if out.returncode != 0:
            return None
        if spec["read"] == "outfile" or (command is not None and outfile):
            answer = Path(outfile).read_text().strip()
        elif spec["read"] == "opencode_json":
            answer = parse_opencode_json(out.stdout) or ""
        else:
            answer = out.stdout.strip()
        return answer or None
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        if outfile:
            try:
                os.unlink(outfile)
            except OSError:
                pass


def draft_block(root: Path, agent: str, *, real_bin: str | None, model: str,
                timeout: float, prev_block: str | None, include_git_diff: bool,
                command: list[str] | None = None) -> tuple[str, dict]:
    """Produce the markdown body for this agent's whiteboard block.

    Always returns something usable: the model's polished version if available,
    otherwise the deterministic skeleton. The second tuple element is a stats
    dict with the char counts of every input section, the output, and a
    `model_called` flag — useful for logging token spend without re-running
    the drafter. Char counts (chars/4 ≈ tokens) intentionally exclude the
    agent bootstrap (system prompt, tool defs, CLAUDE.md) since that varies
    by agent and isn't visible in the prompt we compose.
    """
    info = collect_changes(root, include_git_diff=include_git_diff)
    skeleton = build_skeleton(info)
    diff_chars = len(info["diff"])
    skeleton_chars = len(skeleton)
    prev_block_chars = len(prev_block) if prev_block else 0
    if not real_bin:
        return skeleton, {
            "diff_chars": diff_chars,
            "skeleton_chars": skeleton_chars,
            "prev_block_chars": prev_block_chars,
            "prompt_chars": 0,
            "output_chars": 0,
            "model_called": False,
        }
    prompt = compose_prompt(prev_block, info, skeleton)
    prompt_chars = len(prompt)
    polished = run_agent(real_bin, agent, prompt, model, timeout, command=command)
    if polished:
        return polished, {
            "diff_chars": diff_chars,
            "skeleton_chars": skeleton_chars,
            "prev_block_chars": prev_block_chars,
            "prompt_chars": prompt_chars,
            "output_chars": len(polished),
            "model_called": True,
        }
    return skeleton, {
        "diff_chars": diff_chars,
        "skeleton_chars": skeleton_chars,
        "prev_block_chars": prev_block_chars,
        "prompt_chars": prompt_chars,
        "output_chars": 0,
        "model_called": False,
    }

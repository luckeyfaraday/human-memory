#!/usr/bin/env python3
"""watcher.py — human-memory freshness engine (MVP, polling).

Spawned detached by agent-shim before it exec's the real agent. Watches the
working tree for file modifications and compares their recency against
HUMAN_MEMORY.md. When real work advances but the whiteboard stands still, the
memory is *stale* — and we say so.

Staleness here is RELATIVE: work moving while memory doesn't. Not absolute age.

MVP scope: detect + warn (to a per-session log). Drafting an update from the
diff is the next step and is intentionally stubbed below.

Lifecycle: exits when the agent process (--agent-pid) exits, so it never
outlives the command that launched it. stdlib only; no inotify (not available
on the target box) — polling is the portable MVP, native inotify is a later
upgrade.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MEMORY_FILE = "HUMAN_MEMORY.md"

# Tunables (will move to config.toml — see README rollout step 4).
POLL_SECONDS = 5
# After this many unrecorded work-edits, nag.
STALE_EDIT_THRESHOLD = 8
# ...or after this long with work advancing and memory frozen.
STALE_SECONDS_THRESHOLD = 180

IGNORE_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".context-workspace",
               ".agent-memory", "dist", "build", "target", ".next", ".cache"}
IGNORE_SUFFIXES = {".pyc", ".log", ".swp", ".tmp"}


def now() -> float:
    return time.time()


if sys.platform == "win32":
    # CAUTION: os.kill(pid, 0) is destructive on Windows — CPython maps every
    # signal except CTRL_C_EVENT/CTRL_BREAK_EVENT to TerminateProcess, so signal
    # 0 would *kill the agent we are watching*. Probe the process with a handle
    # and a zero-timeout wait instead (non-destructive): WAIT_TIMEOUT means the
    # process is still running, WAIT_OBJECT_0 means it has exited.
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.windll.kernel32
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    _SYNCHRONIZE = 0x00100000
    _WAIT_TIMEOUT = 0x00000102

    def agent_alive(pid: int) -> bool:
        handle = _kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return False  # gone (or never existed / no access)
        try:
            return _kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            _kernel32.CloseHandle(handle)
else:
    def agent_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists, just not ours to signal
        return True


def scan_tree(root: Path) -> tuple[float, int]:
    """Return (newest_mtime_of_work_files, count_of_work_files).

    Excludes HUMAN_MEMORY.md itself and ignored dirs/suffixes.
    """
    newest = 0.0
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn == MEMORY_FILE:
                continue
            if any(fn.endswith(s) for s in IGNORE_SUFFIXES):
                continue
            p = Path(dirpath) / fn
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            count += 1
            if m > newest:
                newest = m
    return newest, count


def memory_mtime(root: Path) -> float | None:
    p = root / MEMORY_FILE
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def log_line(log_path: Path, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with log_path.open("a") as f:
            f.write(f"{ts} {msg}\n")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--agent-pid", type=int, required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--log-dir", required=True)
    args = ap.parse_args()

    root = Path(args.cwd)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    session = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"{args.agent}-{session}-{args.agent_pid}.log"

    log_line(log_path, f"watcher start agent={args.agent} pid={args.agent_pid} cwd={root}")

    if not (root / MEMORY_FILE).exists():
        log_line(log_path, f"note: no {MEMORY_FILE} in {root} — agent has no whiteboard yet")

    # Baseline: edits that happen AFTER the last memory update are "unrecorded".
    last_seen_newest, _ = scan_tree(root)
    unrecorded_edits = 0
    last_mem_mtime = memory_mtime(root)
    last_nag = 0.0
    work_started_at: float | None = None

    while agent_alive(args.agent_pid):
        time.sleep(POLL_SECONDS)

        newest, _count = scan_tree(root)
        mem_mtime = memory_mtime(root)

        # Did the whiteboard get updated? Reset the staleness accounting.
        if mem_mtime is not None and (last_mem_mtime is None or mem_mtime > last_mem_mtime):
            if unrecorded_edits:
                log_line(log_path, f"fresh: {MEMORY_FILE} updated, clearing "
                                   f"{unrecorded_edits} unrecorded edit(s)")
            last_mem_mtime = mem_mtime
            unrecorded_edits = 0
            work_started_at = None

        # Did work advance since we last looked?
        if newest > last_seen_newest:
            unrecorded_edits += 1
            last_seen_newest = newest
            if work_started_at is None:
                work_started_at = now()

        # Is the memory stale relative to the work?
        work_age = (now() - work_started_at) if work_started_at else 0
        stale = unrecorded_edits >= STALE_EDIT_THRESHOLD or (
            work_started_at is not None and work_age >= STALE_SECONDS_THRESHOLD
            and unrecorded_edits > 0
        )

        if stale and (now() - last_nag) > STALE_SECONDS_THRESHOLD:
            last_nag = now()
            msg = (f"STALE: {unrecorded_edits} edit(s) since {MEMORY_FILE} last moved "
                   f"({int(work_age)}s of unrecorded work). Whiteboard is behind.")
            log_line(log_path, msg)
            # NEXT: draft a suggested HUMAN_MEMORY.md update from the diff.
            # draft_update(root, log_path)  # stubbed — see README known limitations

    log_line(log_path, "watcher stop (agent exited)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

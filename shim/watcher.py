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
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MEMORY_FILE = "HUMAN_MEMORY.md"


@dataclass(frozen=True)
class Config:
    """Watcher tunables. Defaults below; overridden by config.toml [watcher]."""
    # How often to scan the tree.
    poll_seconds: float = 5
    # Nag after this many unrecorded work-edits...
    stale_edit_threshold: int = 8
    # ...or after this long with work advancing and memory frozen.
    stale_seconds_threshold: float = 180
    ignore_dirs: frozenset[str] = field(default_factory=lambda: frozenset({
        ".git", "node_modules", ".venv", "__pycache__", ".context-workspace",
        ".agent-memory", "dist", "build", "target", ".next", ".cache"}))
    ignore_suffixes: frozenset[str] = field(default_factory=lambda: frozenset({
        ".pyc", ".log", ".swp", ".tmp"}))


def default_config_path() -> Path:
    """~/.agent-memory/config.toml, honoring AGENT_MEMORY_HOME like the shim does."""
    home = os.environ.get("AGENT_MEMORY_HOME") or os.path.join(Path.home(), ".agent-memory")
    return Path(home) / "config.toml"


def load_config(path: Path | None = None) -> tuple[Config, str | None]:
    """Load Config from a config.toml [watcher] table.

    Returns (config, note). `note` is a human-readable string describing what
    happened (missing file, parse error, which keys were applied) so the caller
    can log it — config problems must never be silent (they'd masquerade as
    "defaults are fine"). Unknown keys are reported, not ignored quietly.
    """
    path = path or default_config_path()
    defaults = Config()
    if not path.exists():
        return defaults, f"no config at {path}; using defaults"
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return defaults, f"config {path} unreadable ({e}); using defaults"

    table = data.get("watcher", {})
    known = {"poll_seconds", "stale_edit_threshold", "stale_seconds_threshold",
             "ignore_dirs", "ignore_suffixes"}
    unknown = set(table) - known
    # Coercing user-supplied values can raise (e.g. int("not-an-int")); a bad
    # config must fall back to defaults, never crash the watcher.
    try:
        kwargs: dict = {}
        if "poll_seconds" in table:
            kwargs["poll_seconds"] = float(table["poll_seconds"])
        if "stale_edit_threshold" in table:
            kwargs["stale_edit_threshold"] = int(table["stale_edit_threshold"])
        if "stale_seconds_threshold" in table:
            kwargs["stale_seconds_threshold"] = float(table["stale_seconds_threshold"])
        if "ignore_dirs" in table:
            kwargs["ignore_dirs"] = frozenset(str(d) for d in table["ignore_dirs"])
        if "ignore_suffixes" in table:
            kwargs["ignore_suffixes"] = frozenset(str(s) for s in table["ignore_suffixes"])
        config = Config(**kwargs)
    except (TypeError, ValueError) as e:
        return defaults, f"config {path} has bad values ({e}); using defaults"

    note = f"loaded config from {path}"
    if kwargs:
        note += f" (applied: {', '.join(sorted(kwargs))})"
    if unknown:
        note += f"; ignored unknown key(s): {', '.join(sorted(unknown))}"
    return config, note


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


def scan_tree(root: Path, ignore_dirs: frozenset[str],
              ignore_suffixes: frozenset[str]) -> tuple[float, int]:
    """Return (newest_mtime_of_work_files, count_of_work_files).

    Excludes HUMAN_MEMORY.md itself and ignored dirs/suffixes.
    """
    newest = 0.0
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith(".")]
        for fn in filenames:
            if fn == MEMORY_FILE:
                continue
            if any(fn.endswith(s) for s in ignore_suffixes):
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
    ap.add_argument("--config", default=None,
                    help="path to config.toml (default: $AGENT_MEMORY_HOME/config.toml)")
    args = ap.parse_args()

    cfg, cfg_note = load_config(Path(args.config) if args.config else None)

    root = Path(args.cwd)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    session = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"{args.agent}-{session}-{args.agent_pid}.log"

    log_line(log_path, f"watcher start agent={args.agent} pid={args.agent_pid} cwd={root}")
    log_line(log_path, f"config: {cfg_note}")

    if not (root / MEMORY_FILE).exists():
        log_line(log_path, f"note: no {MEMORY_FILE} in {root} — agent has no whiteboard yet")

    # Baseline: edits that happen AFTER the last memory update are "unrecorded".
    last_seen_newest, _ = scan_tree(root, cfg.ignore_dirs, cfg.ignore_suffixes)
    unrecorded_edits = 0
    last_mem_mtime = memory_mtime(root)
    last_nag = 0.0
    work_started_at: float | None = None

    while agent_alive(args.agent_pid):
        time.sleep(cfg.poll_seconds)
        # Re-check before doing work: if the agent died during the sleep, exit
        # now instead of burning one last scan against a dead session.
        if not agent_alive(args.agent_pid):
            break

        newest, _count = scan_tree(root, cfg.ignore_dirs, cfg.ignore_suffixes)
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
        stale = unrecorded_edits >= cfg.stale_edit_threshold or (
            work_started_at is not None and work_age >= cfg.stale_seconds_threshold
            and unrecorded_edits > 0
        )

        if stale and (now() - last_nag) > cfg.stale_seconds_threshold:
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

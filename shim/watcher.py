#!/usr/bin/env python3
"""watcher.py — human-memory freshness engine (MVP, polling).

Spawned detached by agent-shim before it exec's the real agent. Watches the
working tree for file modifications and compares their recency against
HUMAN_MEMORY.md. When real work advances but the whiteboard stands still, the
memory is *stale* — and we say so.

Staleness here is RELATIVE: work moving while memory doesn't. Not absolute age.

MVP scope: detect + warn (to a per-session log). Optional auto-drafting can
also update this session's fenced block when enabled in config.

Lifecycle: exits when the agent process (--agent-pid) exits, so it never
outlives the command that launched it. stdlib only; no inotify (not available
on the target box) — polling is the portable MVP, native inotify is a later
upgrade.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Sibling modules (same dir in repo and in ~/.agent-memory/lib). Guarded so the
# watcher still runs if the drafter pieces aren't installed.
try:
    import drafter
    import memory_store
    import whiteboard
except ImportError:
    drafter = None
    memory_store = None
    whiteboard = None

MEMORY_FILE = "HUMAN_MEMORY.md"
VALID_MEMORY_STORAGE = {"central", "project-file"}


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
    # --- [drafter]: auto-author HUMAN_MEMORY.md. OFF by default — it calls a
    #     model in the background and spends the user's tokens. ---
    draft_enabled: bool = False
    draft_model: str = "haiku"           # Claude-only; Codex/OpenCode use their configured default
    draft_quiescence_seconds: float = 300  # draft after work has been idle this long
    draft_timeout_seconds: float = 60    # hard cap on the drafter subprocess
    draft_include_git_diff: bool = True
    draft_min_edit_ticks: int = 6        # avoid LLM calls for tiny pauses
    draft_min_diff_chars: int = 200      # skip LLM call when the diff is trivially small
    draft_max_drafts_per_session: int = 2  # mid-session drafts; exit draft is separate
    draft_always_on_exit: bool = False   # capture final state only when the user opts in
    draft_command: tuple[str, ...] | None = None
    memory_storage: str = "central"


def default_config_path() -> Path:
    """~/.agent-memory/config.toml, honoring AGENT_MEMORY_HOME like the shim does."""
    if memory_store is not None:
        return memory_store.default_config_path()
    home = os.environ.get("AGENT_MEMORY_HOME") or os.path.join(Path.home(), ".agent-memory")
    return Path(home) / "config.toml"


def _config_bool(value, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a TOML boolean")
    return value


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
    drafter = data.get("drafter", {})
    memory = data.get("memory", {})
    if not isinstance(table, dict) or not isinstance(drafter, dict) or not isinstance(memory, dict):
        return defaults, f"config {path} has bad table values; using defaults"
    known = {"poll_seconds", "stale_edit_threshold", "stale_seconds_threshold",
             "ignore_dirs", "ignore_suffixes"}
    known_drafter = {"enabled", "model", "quiescence_seconds", "timeout_seconds",
                     "include_git_diff", "min_edit_ticks", "min_diff_chars",
                     "max_drafts_per_session", "always_on_exit", "command"}
    known_memory = {"storage"}
    unknown = (
        (set(table) - known)
        | {f"drafter.{k}" for k in set(drafter) - known_drafter}
        | {f"memory.{k}" for k in set(memory) - known_memory}
    )
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
        if "enabled" in drafter:
            kwargs["draft_enabled"] = _config_bool(drafter["enabled"], "drafter.enabled")
        if "model" in drafter:
            kwargs["draft_model"] = str(drafter["model"])
        if "quiescence_seconds" in drafter:
            kwargs["draft_quiescence_seconds"] = float(drafter["quiescence_seconds"])
        if "timeout_seconds" in drafter:
            kwargs["draft_timeout_seconds"] = float(drafter["timeout_seconds"])
        if "include_git_diff" in drafter:
            kwargs["draft_include_git_diff"] = _config_bool(
                drafter["include_git_diff"], "drafter.include_git_diff")
        if "min_edit_ticks" in drafter:
            kwargs["draft_min_edit_ticks"] = int(drafter["min_edit_ticks"])
        if "min_diff_chars" in drafter:
            kwargs["draft_min_diff_chars"] = int(drafter["min_diff_chars"])
        if "max_drafts_per_session" in drafter:
            kwargs["draft_max_drafts_per_session"] = int(drafter["max_drafts_per_session"])
        if "always_on_exit" in drafter:
            kwargs["draft_always_on_exit"] = _config_bool(
                drafter["always_on_exit"], "drafter.always_on_exit")
        if "command" in drafter:
            cmd = drafter["command"]
            if not isinstance(cmd, list) or not all(isinstance(p, str) for p in cmd):
                raise ValueError("drafter.command must be an array of strings")
            kwargs["draft_command"] = tuple(cmd)
        if "storage" in memory:
            kwargs["memory_storage"] = str(memory["storage"])
        config = Config(**kwargs)
        if config.memory_storage not in VALID_MEMORY_STORAGE:
            raise ValueError("memory.storage must be central or project-file")
        if config.draft_quiescence_seconds < 0:
            raise ValueError("drafter.quiescence_seconds must be >= 0")
        if config.draft_min_edit_ticks < 1:
            raise ValueError("drafter.min_edit_ticks must be >= 1")
        if config.draft_min_diff_chars < 0:
            raise ValueError("drafter.min_diff_chars must be >= 0")
        if config.draft_max_drafts_per_session < 0:
            raise ValueError("drafter.max_drafts_per_session must be >= 0")
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


def memory_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def log_line(log_path: Path, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with log_path.open("a") as f:
            f.write(f"{ts} {msg}\n")
    except OSError:
        pass


def write_state(state_path: Path, payload: dict) -> None:
    """Publish this watcher's live status as JSON for `human-memory` to read.

    Written atomically (tmp + replace) on every poll so a reader never sees a
    half-written file. Best-effort: surfacing must never break the watcher.
    """
    payload = {**payload, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(state_path)
    except OSError:
        pass


def clear_state(state_path: Path) -> None:
    try:
        state_path.unlink()
    except OSError:
        pass


def session_owner(agent: str, pid: int) -> str:
    """Stable whiteboard owner for one running agent process."""
    return f"{agent}-{pid}"


def bootstrap_missing_memory(root: Path, agent: str, owner: str,
                             include_git_diff: bool,
                             memory_path: Path | None = None) -> bool:
    """Create a first whiteboard block without spending model tokens.

    A missing HUMAN_MEMORY.md should not leave a newly shimmed session stuck in
    "NO FILE". The initial block is deterministic-only; normal quiescence/exit
    drafts can polish later once actual work has happened.
    """
    if drafter is None or whiteboard is None:
        return False
    mem_path = memory_path or root / MEMORY_FILE
    if mem_path.exists():
        return False
    body, _stats = drafter.draft_block(
        root, agent, real_bin=None, model="", timeout=0,
        prev_block=None, include_git_diff=include_git_diff)
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    whiteboard.update_file(mem_path, owner, body)
    return True


def should_draft_quiescence(*, drafting: bool, unrecorded_edits: int,
                            last_edit_at: float | None, last_seen_newest: float,
                            last_drafted_newest: float, draft_count: int,
                            diff_chars: int, cfg: Config,
                            now_value: float) -> bool:
    """Return whether a mid-session quiescence draft should run."""
    return (
        drafting
        and unrecorded_edits >= cfg.draft_min_edit_ticks
        and last_edit_at is not None
        and last_seen_newest != last_drafted_newest
        and draft_count < cfg.draft_max_drafts_per_session
        and (now_value - last_edit_at) >= cfg.draft_quiescence_seconds
        and diff_chars >= cfg.draft_min_diff_chars
    )


def should_draft_exit(*, drafting: bool, unrecorded_edits: int,
                      last_seen_newest: float, last_drafted_newest: float,
                      diff_chars: int, cfg: Config) -> bool:
    """Return whether the final checkpoint should draft on agent exit."""
    return (
        drafting
        and cfg.draft_always_on_exit
        and unrecorded_edits > 0
        and last_seen_newest != last_drafted_newest
        and diff_chars >= cfg.draft_min_diff_chars
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--agent-pid", type=int, required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--config", default=None,
                    help="path to config.toml (default: $AGENT_MEMORY_HOME/config.toml)")
    ap.add_argument("--real-bin", default=None,
                    help="absolute path to the real agent binary, for the drafter "
                         "to call headlessly (bypasses the shim)")
    args = ap.parse_args()

    cfg, cfg_note = load_config(Path(args.config) if args.config else None)

    root = Path(args.cwd)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    session = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"{args.agent}-{session}-{args.agent_pid}.log"
    # Live status lives beside the logs (…/log → …/state) so `human-memory` can
    # read every active session. Keyed by agent+pid: unique per watcher.
    state_path = log_dir.parent / "state" / f"{args.agent}-{args.agent_pid}.json"
    owner = session_owner(args.agent, args.agent_pid)

    log_line(log_path, f"watcher start agent={args.agent} pid={args.agent_pid} "
                       f"owner={owner} cwd={root}")
    log_line(log_path, f"config: {cfg_note}")

    if memory_store is not None:
        mem_location = memory_store.resolve(root, cfg.memory_storage)
        mem_path = mem_location.path
        try:
            memory_store.ensure_location(mem_location)
        except OSError as e:
            log_line(log_path, f"memory metadata unavailable: {e!r}")
        log_line(log_path, f"memory storage={mem_location.storage} "
                           f"project={mem_location.project_id} path={mem_path}")
    else:
        mem_location = None
        mem_path = root / MEMORY_FILE
        log_line(log_path, "memory_store.py missing; falling back to project-file storage")

    if not mem_path.exists():
        log_line(log_path, f"note: no {MEMORY_FILE} at {mem_path} — agent has no whiteboard yet")

    drafting = cfg.draft_enabled and drafter is not None and whiteboard is not None
    if cfg.draft_enabled and not drafting:
        log_line(log_path, "drafter enabled but drafter/whiteboard modules missing — disabled")
    elif drafting:
        kind = "hybrid (LLM)" if args.real_bin else "deterministic skeleton (no --real-bin)"
        if args.agent == "claude":
            model_note = f"claude_model={cfg.draft_model}"
        elif drafter and args.agent in drafter.DEFAULT_COMMANDS:
            agent_model = drafter.DEFAULT_COMMANDS[args.agent].get("agent_model")
            model_note = f"{args.agent}_model={agent_model}" if agent_model else (
                f"{args.agent} default model")
        else:
            model_note = f"{args.agent} default model"
        log_line(log_path, f"drafter ON: {kind}, {model_note}, "
                           f"quiescence={cfg.draft_quiescence_seconds}s")
        if bootstrap_missing_memory(
                root, args.agent, owner, cfg.draft_include_git_diff, memory_path=mem_path):
            log_line(log_path, f"bootstrapped {MEMORY_FILE} at {mem_path} "
                               f"with deterministic {owner} block")

    # Baseline: edits that happen AFTER the last memory update are "unrecorded".
    last_seen_newest, _ = scan_tree(root, cfg.ignore_dirs, cfg.ignore_suffixes)
    unrecorded_edits = 0
    last_mem_mtime = memory_mtime(mem_path)
    last_nag = 0.0
    work_started_at: float | None = None
    last_edit_at: float | None = None
    last_drafted_newest = last_seen_newest
    draft_count = 0

    def publish(stale: bool, work_age: float) -> None:
        write_state(state_path, {
            "agent": args.agent,
            "pid": args.agent_pid,
            "cwd": str(root),
            "stale": stale,
            "unrecorded_edits": unrecorded_edits,
            "work_age_s": int(work_age),
            "whiteboard_exists": mem_path.exists(),
            "memory_path": str(mem_path),
            "memory_storage": mem_location.storage if mem_location else "project-file",
            "project_id": mem_location.project_id if mem_location else None,
        })

    def do_draft(label: str) -> None:
        """Author this agent's block from the work since the last draft. Records
        the work (resets staleness). Best-effort — never breaks the watcher."""
        nonlocal unrecorded_edits, work_started_at, last_mem_mtime, last_drafted_newest
        nonlocal draft_count
        try:
            content = mem_path.read_text() if mem_path.exists() else ""
        except OSError:
            content = ""
        try:
            body, stats = drafter.draft_block(
                root, args.agent, real_bin=args.real_bin, model=cfg.draft_model,
                timeout=cfg.draft_timeout_seconds,
                prev_block=whiteboard.extract_agent_block(content, owner),
                include_git_diff=cfg.draft_include_git_diff,
                command=list(cfg.draft_command) if cfg.draft_command else None)
            if mem_path.exists():  # one-revert-away safety net
                shutil.copy2(mem_path, mem_path.with_name(mem_path.name + ".bak"))
            whiteboard.update_file(mem_path, owner, body)
        except Exception as e:  # noqa: BLE001 — drafting must never crash the watcher
            log_line(log_path, f"draft ({label}) failed: {e!r}")
            return
        # The work is now recorded; clear staleness accounting.
        unrecorded_edits = 0
        work_started_at = None
        last_drafted_newest = last_seen_newest
        last_mem_mtime = memory_mtime(mem_path)
        if label != "exit":
            draft_count += 1
        # Per-draft token-relevant metrics, so the user can see what each call
        # cost without re-running the drafter. Char counts (~chars/4 ≈ tokens);
        # agent-bootstrap overhead is not visible here.
        metrics = (
            f"diff={stats['diff_chars']} prev={stats['prev_block_chars']} "
            f"prompt={stats['prompt_chars']} out={stats['output_chars']} "
            f"model={'yes' if stats['model_called'] else 'no'}"
        )
        log_line(log_path, f"drafted ({label}): updated {owner} block in {mem_path} "
                           f"[{metrics}]")

    publish(stale=False, work_age=0)
    try:
        while agent_alive(args.agent_pid):
            time.sleep(cfg.poll_seconds)
            # Re-check before doing work: if the agent died during the sleep,
            # exit now instead of burning one last scan against a dead session.
            if not agent_alive(args.agent_pid):
                break

            newest, _count = scan_tree(root, cfg.ignore_dirs, cfg.ignore_suffixes)
            mem_mtime = memory_mtime(mem_path)

            # Did the whiteboard get updated (by a human/agent)? Reset accounting.
            if mem_mtime is not None and (last_mem_mtime is None or mem_mtime > last_mem_mtime):
                if unrecorded_edits:
                    log_line(log_path, f"fresh: {mem_path} updated, clearing "
                                       f"{unrecorded_edits} unrecorded edit(s)")
                last_mem_mtime = mem_mtime
                unrecorded_edits = 0
                work_started_at = None
                last_drafted_newest = last_seen_newest

            # Did work advance since we last looked?
            if newest > last_seen_newest:
                unrecorded_edits += 1
                last_seen_newest = newest
                last_edit_at = now()
                if work_started_at is None:
                    work_started_at = now()

            # Is the memory stale relative to the work?
            work_age = (now() - work_started_at) if work_started_at else 0
            stale = unrecorded_edits >= cfg.stale_edit_threshold or (
                work_started_at is not None and work_age >= cfg.stale_seconds_threshold
                and unrecorded_edits > 0
            )

            publish(stale=stale, work_age=work_age)

            if stale and (now() - last_nag) > cfg.stale_seconds_threshold:
                last_nag = now()
                log_line(log_path,
                         f"STALE: {unrecorded_edits} edit(s) since {mem_path} last moved "
                         f"({int(work_age)}s of unrecorded work). Whiteboard is behind.")

            # Quiescence draft: work happened, then settled — record it now. One
            # draft per settled chunk (last_drafted_newest gate).
            diff_chars = 0
            if drafting and last_edit_at is not None and drafter is not None:
                try:
                    info = drafter.collect_changes(root,
                                                   include_git_diff=cfg.draft_include_git_diff)
                    diff_chars = len(info["diff"])
                except Exception:  # noqa: BLE001 — gating must never crash the watcher
                    pass
            if should_draft_quiescence(
                    drafting=drafting,
                    unrecorded_edits=unrecorded_edits,
                    last_edit_at=last_edit_at,
                    last_seen_newest=last_seen_newest,
                    last_drafted_newest=last_drafted_newest,
                    draft_count=draft_count,
                    diff_chars=diff_chars,
                    cfg=cfg,
                    now_value=now()):
                do_draft("quiescence")
    finally:
        # Final checkpoint: capture any work that never settled before exit.
        exit_diff_chars = 0
        if drafting and drafter is not None:
            try:
                info = drafter.collect_changes(root,
                                               include_git_diff=cfg.draft_include_git_diff)
                exit_diff_chars = len(info["diff"])
            except Exception:  # noqa: BLE001
                pass
        if should_draft_exit(
                drafting=drafting,
                unrecorded_edits=unrecorded_edits,
                last_seen_newest=last_seen_newest,
                last_drafted_newest=last_drafted_newest,
                diff_chars=exit_diff_chars,
                cfg=cfg):
            do_draft("exit")
        # Always retract our status so `human-memory` never shows a dead session.
        clear_state(state_path)

    log_line(log_path, "watcher stop (agent exited)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

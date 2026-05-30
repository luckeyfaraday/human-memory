#!/usr/bin/env python3
"""whiteboard.py — concurrency-safe, per-agent edits to HUMAN_MEMORY.md.

When several agents share one working tree (it happens), they must not clobber
each other's whiteboard. The rule that makes that safe:

  * Any UNFENCED content is sacred — it's the human's, never auto-touched.
  * Each automated writer owns exactly one fenced block, delimited by
        <!-- hm:agent=NAME -->
        ...the agent's section...
        <!-- /hm:agent=NAME -->
    and only ever rewrites *its own* block.

Disjoint regions → no lost updates when two agents write at once; separate
blocks → separate git hunks → no merge conflicts. A single agent that never
fences stays byte-identical to a plain hand-written file.

This is the primitive `draft_update()` will call. stdlib only.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

AGENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _markers(agent: str) -> tuple[str, str]:
    return f"<!-- hm:agent={agent} -->", f"<!-- /hm:agent={agent} -->"


def replace_agent_block(content: str, agent: str, body: str | None) -> str:
    """Return `content` with `agent`'s fenced block set to `body`.

    body=None removes the block. Everything outside the agent's own block —
    including other agents' blocks and any unfenced human text — is preserved
    byte-for-byte. Pure function; no I/O. Idempotent for a fixed body.
    """
    if not AGENT_RE.match(agent):
        raise ValueError(f"unsafe agent name: {agent!r}")
    start, end = _markers(agent)
    block_re = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)

    if body is None:
        # Remove the block (and a trailing blank line it leaves behind).
        return re.sub(block_re.pattern + r"\n?", "", content, flags=re.DOTALL)

    new_block = f"{start}\n{body.strip()}\n{end}"
    if block_re.search(content):
        return block_re.sub(lambda _m: new_block, content, count=1)

    # Append a fresh block, separated by a blank line from existing content.
    sep = "" if content == "" else ("\n" if content.endswith("\n") else "\n\n")
    tail = "" if content == "" else "\n"
    return f"{content}{sep}{new_block}{tail}" if content else f"{new_block}\n"


class _FileLock:
    """Portable advisory lock via O_CREAT|O_EXCL — works the same on POSIX and
    Windows, no fcntl/msvcrt split. Steals a lock older than `stale_after` so a
    crashed writer can't wedge the file forever."""

    def __init__(self, target: Path, timeout: float = 5.0, stale_after: float = 30.0):
        self.lock_path = target.with_suffix(target.suffix + ".lock")
        self.timeout = timeout
        self.stale_after = stale_after
        self._fd: int | None = None

    def __enter__(self) -> "_FileLock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                # Break a stale lock left by a dead writer.
                try:
                    if time.time() - self.lock_path.stat().st_mtime > self.stale_after:
                        os.unlink(self.lock_path)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"could not acquire {self.lock_path}")
                time.sleep(0.02)

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            os.unlink(self.lock_path)
        except OSError:
            pass


def update_file(path: Path, agent: str, body: str | None, *, timeout: float = 5.0) -> None:
    """Set `agent`'s block in the HUMAN_MEMORY.md at `path`, concurrency-safe.

    Locked read-modify-write with an atomic replace, so simultaneous writers for
    different agents never lose each other's updates.
    """
    path = Path(path)
    with _FileLock(path, timeout=timeout):
        try:
            content = path.read_text()
        except FileNotFoundError:
            content = ""
        new = replace_agent_block(content, agent, body)
        if new == content:
            return
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new)
        tmp.replace(path)

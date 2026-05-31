# HUMAN_MEMORY.md format — single- and multi-agent

**Status:** Adopted. Implemented by `shim/whiteboard.py` (`replace_agent_block`,
`update_file`) and tested in `tests/test_whiteboard.py`.

## Single agent

One whiteboard file, five fixed sections in fixed order. By default the file
lives in central storage under `~/.agent-memory/projects/<project-id>/`; with
`[memory].storage = "project-file"` it lives at the working-tree root.

```markdown
# HUMAN_MEMORY.md

## Current State
## What Just Happened
## Pending
## Key Decisions
## Where I Left Off
```

A human (or a lone agent) editing by hand writes exactly this. No markers, no
ceremony. The file format is byte-for-byte what the project shipped on day one;
only the default storage location changed.

## Multiple sessions in the same working tree

This happens (sometimes you point several agents, or several sessions of the
same agent, at one tree). The rule that keeps them from clobbering each other:

> **Unfenced content is sacred. Each automated writer owns exactly one fenced
> block and only ever rewrites its own.**

A block is delimited by HTML comments (invisible when rendered):

```markdown
# HUMAN_MEMORY.md

any unfenced text here is the human's — agents never touch it

<!-- hm:session=claude-10101 -->
## Current State
…claude session 10101's five sections…
<!-- /hm:session=claude-10101 -->

<!-- hm:session=codex-10202 -->
## Current State
…codex session 10202's five sections…
<!-- /hm:session=codex-10202 -->
```

### Why this shape

- **No live clobber.** Two sessions writing at once touch disjoint byte ranges. The
  writer takes a short lock and does an atomic read-modify-write of *only* its own
  block (`update_file`), so simultaneous updates can't lose each other.
- **No git conflict.** Distinct blocks are distinct hunks, so parallel branches
  merge cleanly instead of fighting over the same lines. The PR #1 clobber — an
  agent overwriting the whole file — becomes structurally impossible for automated
  writers.
- **No ownership ambiguity.** Because unfenced text is never auto-edited, there's
  no question of "who owns the existing plain content" when a second agent appears.
  The human's notes and each session's block coexist. Three Codex sessions get
  three separate blocks (`codex-<pid>`), not one last-writer-wins Codex block.
- **Degrades to nothing.** One agent that never fences = a plain file = the
  single-agent format above. The machinery only appears when a second automated
  writer does.

### Cross-agent view

You don't read three blocks by hand to understand a swarm — `human-memory` does
that: `human-memory status` shows every running agent and its staleness across all
directories, and `human-memory show` prints a tree's whiteboard. File-level
partitioning is for *write safety*; the swarm overview lives in the viewer.

## Programmatic access

```python
from whiteboard import update_file   # ships to ~/.agent-memory/lib/
update_file(path, "claude-10101", body)    # set one session block (locked, atomic)
update_file(path, "claude-10101", None)    # remove it
```

or from the shell, concurrency-safe:

```bash
echo "## Current State
…" | human-memory set claude-10101
```

This is the primitive a future watcher-driven `draft_update()` will call (see
[llm-drafter-design.md](llm-drafter-design.md)), so automated drafting inherits
the same no-clobber guarantee for free.

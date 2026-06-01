# LLM-backed auto-drafting design

**Status:** Implemented, opt-in. Built in `shim/drafter.py` and the drafting
loop in `shim/watcher.py`; covered by `tests/test_drafter.py` and watcher config
tests. Off by default (`[drafter] enabled = false`) because it spends the user's
tokens in the background.

**Last updated:** 2026-06-01

---

## Purpose

The shim and watcher make freshness visible when `HUMAN_MEMORY.md` falls behind
active work. Optional auto-drafting goes one step further: when enabled, the
watcher writes this session's fenced block from observed file changes so the
human-readable whiteboard stays useful without relying on a foreground agent to
remember a prompt convention.

The shim remains out of the agent I/O path. It starts the watcher, then hands off
to the real agent process; drafting is a watcher-side, best-effort activity.

## Current architecture

- **Hybrid drafting.** `drafter.collect_changes()` gathers deterministic facts:
  changed filenames, tracked-file TODO/FIXME markers, newest changed file, and a
  bounded tracked git diff. `build_skeleton()` turns those facts into the five
  required whiteboard sections. If no model is available, the skeleton is the
  final output.
- **Headless polish.** If `--real-bin` is available, the watcher invokes the
  user's already-authenticated real agent in headless mode to polish the
  skeleton. Failures, empty output, non-zero exits, or timeouts fall back to the
  skeleton.
- **Per-session direct writes.** Drafts are written directly to the live
  `HUMAN_MEMORY.md` location as this session's owner-scoped fenced block via
  `whiteboard.update_file()`. Unfenced human content and other sessions' blocks
  are preserved byte-for-byte.
- **Backup before update.** Before updating an existing memory file, the watcher
  copies it to `HUMAN_MEMORY.md.bak` as a one-revert-away safety net.
- **Central storage aware.** The watcher resolves the memory file through
  `memory_store.py`, so the default location is under
  `$AGENT_MEMORY_HOME/projects/<project-id>/HUMAN_MEMORY.md`. Project-file mode
  remains available with `[memory] storage = "project-file"`.

## Backend decisions

The backend is the same agent binary the user launched, called through the
resolved real binary rather than the shim:

- `claude`: `claude -p <prompt> --model <model>`; answer on stdout. The shared
  `[drafter].model` value applies here.
- `codex`: `codex exec --sandbox read-only --skip-git-repo-check --model
  gpt-5.4-mini -o <file> <prompt>`; answer read from the output file because
  stdout is status chrome. stdin is closed to avoid blocking.
- `opencode`: `opencode run --format json <prompt>`; answer parsed from JSONL
  `type: "text"` events. It uses opencode's configured default model.

Users can override the whole argv with `[drafter].command`. Placeholders include
`{prompt}`, `{model}`, `{agent_model}`, `{bin}`, and `{outfile}`. If an override
uses `{outfile}`, the runner creates a temporary file and reads the final answer
from it.

This design avoids direct API keys, keeps the provider boundary aligned with the
agent the user chose, and adds no runtime dependencies beyond Python stdlib and
the installed agent binary.

## Cadence and limits

Drafting is quiescence-triggered, not continuous:

1. The watcher observes work edits after the last memory update.
2. A mid-session draft can run after `min_edit_ticks` and
   `quiescence_seconds` of no new edits, up to `max_drafts_per_session`.
3. A final exit draft can capture remaining work when `always_on_exit = true`.

This creates settled checkpoints instead of spending tokens on every poll.

## Safety and privacy boundaries

- Drafting is opt-in and config-driven.
- The drafter prompt explicitly tells the model not to invent decision rationales
  and to omit uncertain decisions.
- Untracked filenames may appear in the deterministic facts, but untracked file
  contents are not scanned for TODO/FIXME markers by default. This avoids sending
  private scratch-file lines to a model provider.
- Diffs are bounded by `MAX_DIFF_CHARS` and memory artifacts such as
  `HUMAN_MEMORY.md`, `HUMAN_MEMORY.md.bak`, and `.agent-memory/` paths are
  filtered from changed files and tracked diffs.
- `AGENT_MEMORY_INTERNAL=1` is set for model subprocesses so accidental shim hits
  do not start nested watchers.

## Historical note: sidecar drafts

An earlier design proposed writing `HUMAN_MEMORY.md.draft` sidecars and promoting
them later. The implemented design supersedes that: the owner-scoped fenced-block
format makes direct writes safe enough because each automated writer can only
replace its own block, while the watcher still controls when writes happen and
keeps a `.bak` safety copy.

## Future work

- Native file notification instead of polling where available.
- Manual hold/promote controls for users who want to freeze or force drafting.
- More integration tests for installed shims and real long-running watcher
  lifecycles.

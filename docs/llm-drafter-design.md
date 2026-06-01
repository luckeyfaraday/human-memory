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
   `quiescence_seconds` of no new edits, when the (memory-artifact-filtered)
   git diff is at least `min_diff_chars` characters, and only up to
   `max_drafts_per_session` per session.
3. A final exit draft can capture remaining work when `always_on_exit = true`
   (off by default — the mid-session draft already covers settled work, and
   the exit call is the most expensive one because it forces a fresh agent
   subprocess for one final read).

This creates settled checkpoints instead of spending tokens on every poll.

## Cost: what each draft actually pays

Each draft invokes the user's full agent as a subprocess (claude / codex /
opencode). The agent then re-loads its system prompt, tool definitions,
`CLAUDE.md`, and any conversation history on every call — typically **~8–15k
tokens of overhead we never see in the drafter prompt**. The drafter prompt
itself is bounded by `MAX_DIFF_CHARS` (≈ 3k tokens worst case) plus a
~200-token skeleton and a ~250-token previous block. Net result: only
**~3–4% of tokens billed per draft** end up as the visible polished output
the user actually reads.

To make this visible, every draft logs its input/output section char counts
alongside the `drafted` line in the watcher log. The skeleton path
(`real_bin = None` or model failure) reports `model_called = no` so you can
see how often the deterministic floor is the answer.

The token-thrift defaults (`quiescence_seconds = 300`, `min_edit_ticks = 6`,
`min_diff_chars = 200`, `always_on_exit = false`) exist because in practice
~95% of an LLM-polished draft is overhead; the skeleton is the floor and
is almost always good enough.

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

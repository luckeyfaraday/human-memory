# HUMAN_MEMORY.md

## Current State
On branch `feat/whiteboard-partition` (stack: #3 config → #4 surface → this).
Built the concurrency-safe per-agent whiteboard updater — the answer to "how do
parallel agents share one HUMAN_MEMORY.md" and the primitive draft_update needs.
Implemented + tested (incl. real multi-process concurrency). Ready to PR.

## What Just Happened
- **Whiteboard partition** (this branch): `shim/whiteboard.py` — automated
  writers own fenced `<!-- hm:agent=X -->` blocks; unfenced human text is sacred;
  each writer does a locked, atomic RMW of only its own block. Single-agent stays
  byte-identical. `tests/test_whiteboard.py` (first tests in the repo): purity,
  idempotency, removal, sacred-text, + two real multi-process concurrency tests
  (6 disjoint agents; 8 writers same agent) — all pass. `human-memory set AGENT`
  exposes it; installers ship whiteboard.py; spec in docs/whiteboard-format.md.
- **Surface staleness** (PR #4): watcher publishes one atomic JSON
  status file per session under `~/.agent-memory/state/`; cleared on exit (and
  pruned if a watcher is killed). New `shim/human-memory` CLI: `status` (live
  table of every agent + stale/fresh/behind), `show` (print a whiteboard),
  `nag` (one-liner for a shell-prompt hook). Both installers install it; bash
  installer prints the optional PROMPT_COMMAND hook.
- **N1 shim-recursion guard**: both `agent-shim` (bash) and `agent-shim.ps1`
  (win) now skip spawning a watcher when `AGENT_MEMORY_INTERNAL=1`, so a future
  watcher-driven drafter calling `claude -p` won't spawn nested watchers.
  Updated the N1 note in `docs/llm-drafter-design.md` to "partial — done".
- Tested: fresh→STALE transition in the table, `nag` fires only in the stale
  dir, state file present during session + gone after exit, N1 guard verified
  (watcher spawns without the env var, not with it).
- (earlier) config.toml support + exit-latency fix (PR #3); bash shim + watcher
  live-tested on real agents (main); Windows port + os.kill fix (PR #1, main);
  llm-drafter design doc (PR #2, main).

## Pending
- [ ] Open PR for feat/whiteboard-partition (stacked on #4)
- [ ] draft_update() per docs/llm-drafter-design.md — DECISIONS MADE (mine, owner
      delegated): quiescence-draft + quiescence-promote, cheap model, hybrid
      deterministic+LLM. Now has its write primitive (whiteboard.update_file) AND
      the N1 env-guard. Still need shim to export the resolved real bin (--real-bin).
- [x] Multi-agent whiteboard merge — DECIDED + BUILT: per-agent fenced blocks,
      locked atomic per-block writes. (was the blocker for draft_update)
- [ ] Polling → native inotify / ReadDirectoryChangesW
- [ ] Resolution override / multi-agent config wiring
- [ ] Code-sign Windows scripts

## Key Decisions
- **Surface via a state dir + viewer, not by printing from the watcher.** The
  watcher is detached from the TTY (and writing into a live TUI would corrupt
  it), so it publishes status to files; `human-memory` (pull) and the
  PROMPT_COMMAND hook (push) read them. Keeps the shim out of the I/O path.
- **State files are self-cleaning**: cleared on exit; the viewer prunes any whose
  PID is dead or whose timestamp is >90s old (covers hard-killed watchers).
- **draft_update direction (delegated to me):** draft at quiescence + on exit
  (NOT continuous — continuous pays per-draft then discards all but the promoted
  one); cheap model (rate-limit contention with the foreground session, not just
  cost); hybrid — fill skeleton deterministically (git diff/stat, TODOs, failing
  tests), LLM only for prose + "Key Decisions". Note the *why* is structurally
  unrecoverable from diffs → LLM will confabulate rationales; accept/flag that.

## Where I Left Off
`shim/human-memory` (new viewer) + `shim/watcher.py` (write_state/clear_state +
publish() in the loop's try/finally). Next: commit + PR this branch. Then the
big one — draft_update() — but resolve the multi-agent merge question first.

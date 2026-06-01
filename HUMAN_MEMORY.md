# HUMAN_MEMORY.md

## Current State
On branch `feat/draft-update` (PR #6, off main). Built `draft_update()` — the
watcher now AUTHORS HUMAN_MEMORY.md (hybrid, quiescence-triggered, opt-in). THEN
verified all three headless agent invocations against the REAL binaries and fixed
the two that were wrong. 22 tests pass. Pushing the fixes to PR #6.

## What Just Happened
- **draft_update()** (this branch): `shim/drafter.py` + a drafting loop in
  watcher.py. Hybrid: deterministic skeleton (`git status`/diff incl. UNTRACKED
  files, newest file, TODOs) is the floor; cheap model polishes into the 5
  sections; on any model failure/timeout → skeleton. Quiescence-triggered (idle
  `quiescence_seconds`, default 25) + a final draft on exit; ONE draft per settled
  chunk. Writes only its own fenced block via whiteboard.update_file, backs up to
  .bak first, calls the REAL bin with AGENT_MEMORY_INTERNAL=1 (N1). OPT-IN
  ([drafter] enabled=false default — it spends the user's tokens). Shim now passes
  --real-bin. `tests/test_drafter.py` (12 tests) + e2e verified both hybrid and
  deterministic-only paths. Decisions resolved in docs/llm-drafter-design.md.
- **Whiteboard partition** (PR #5, merged): `shim/whiteboard.py` — fenced per-agent
  blocks, locked atomic RMW; unfenced human text sacred. 7 tests incl. real
  multi-process concurrency. spec: docs/whiteboard-format.md.
- **Surface staleness** (PR #4, merged): watcher publishes one atomic JSON
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
- [x] draft_update() — BUILT + tested + e2e verified. Opt-in. (PR #6)
- [x] VALIDATE codex/opencode headless commands — DONE, real runs 2026-05-31.
      Both guesses were WRONG; fixed: codex needs `-o <file>` (stdout is chrome)
      + stdin=DEVNULL (else hangs); opencode needs `--format json` (emits NOTHING
      to a pipe otherwise) + parse JSONL; shared model only applies to claude.
      All three now return clean answers through run_agent.
- [ ] Optional: `.human-memory-promote` / `-hold` manual overrides (deferred in doc)
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
draft_update() done on `feat/draft-update`: `shim/drafter.py` (collect_changes /
build_skeleton / compose_prompt / run_agent / draft_block) + the `do_draft()`
helper and quiescence/exit triggers in watcher.py's loop. 19 tests pass; install
+ shim wiring done. Next: commit + open PR (off main). After merge, the honest
follow-up is validating the codex/opencode headless commands against real runs.

<!-- hm:session=codex-613244 -->
## Current State
- Draft-update hardening is in progress in [`shim/drafter.py`](/home/alan/home_ai/projects/human-memory/shim/drafter.py#L156), [`shim/watcher.py`](/home/alan/home_ai/projects/human-memory/shim/watcher.py#L84), and [`tests/test_drafter.py`](/home/alan/home_ai/projects/human-memory/tests/test_drafter.py#L54).
- Focus is safer git-status parsing, stricter config validation, and edge-case coverage for path handling.

## What Just Happened
- [`shim/drafter.py`](/home/alan/home_ai/projects/human-memory/shim/drafter.py#L156): added `_git_bytes()` and `_parse_status_z()`; `collect_changes()` now reads `git status --porcelain=v1 -z`, keeps untracked files in `changed_files`, but skips untracked content when scanning TODO/FIXME; diff filtering now parses section paths and renames instead of trusting the header.
- [`shim/drafter.py`](/home/alan/home_ai/projects/human-memory/shim/drafter.py#L289): `run_agent()` now allocates an outfile whenever `argv` references `{outfile}`, and cleans up on placeholder expansion failure.
- [`shim/watcher.py`](/home/alan/home_ai/projects/human-memory/shim/watcher.py#L84): config booleans are now strict TOML booleans via `_config_bool()` for `drafter.enabled`, `include_git_diff`, and `always_on_exit`.
- [`tests/test_drafter.py`](/home/alan/home_ai/projects/human-memory/tests/test_drafter.py#L54): added coverage for untracked scratch TODO suppression, filenames with spaces/arrows, and `{outfile}` handling.

## Pending
- [ ] Re-run `tests/test_drafter.py` after the git-status and outfile changes.
- [ ] Check the new status parser against a real rename/untracked-space-path repo.
- [ ] Decide whether the stricter TOML boolean validation needs user-facing config docs.

## Key Decisions
- Use NUL-delimited git porcelain to preserve filenames exactly.
- Do not scan untracked files for TODO/FIXME text in git repos.
- Require real booleans in watcher config instead of coercing with `bool()`.
- Create temp outfiles when any command template needs `{outfile}`, not just outfile-read agents.

## Where I Left Off
- Most recent work is in [`shim/drafter.py`](/home/alan/home_ai/projects/human-memory/shim/drafter.py#L156) around `_parse_status_z()`, `collect_changes()`, and `run_agent()`, with follow-up validation in [`tests/test_drafter.py`](/home/alan/home_ai/projects/human-memory/tests/test_drafter.py#L54) and [`shim/watcher.py`](/home/alan/home_ai/projects/human-memory/shim/watcher.py#L84).
<!-- /hm:session=codex-613244 -->

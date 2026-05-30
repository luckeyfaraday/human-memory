# HUMAN_MEMORY.md

## Current State
On branch `feat/config-toml`: added `config.toml` support to the watcher (was
hardcoded constants). Implemented, unit-tested, and verified end-to-end. About
to commit + open a PR. `main` already has the bash shim + the merged Windows port.

## What Just Happened
- **config.toml parsing** (this branch): `watcher.py` now loads `[watcher]`
  tunables (poll/thresholds/ignore-globs) via stdlib `tomllib`. Bad/missing/
  malformed config falls back to defaults and logs why — never crashes. Shipped
  `shim/config.toml.example`; both installers seed it without clobbering an
  existing one. Tested: missing file, valid, unknown key, bad value, malformed
  TOML, empty table; example provably equals built-in defaults.
- Fixed the ~5s watcher exit-latency I'd noted: re-check liveness right after the
  sleep and break, skipping one wasted post-death scan (measured ~64ms exit).
- (earlier, main) Bash shim + watcher built and live-smoke-tested against the
  REAL claude/codex/opencode: resolution, arg/exit transparency, watcher
  lifecycle, staleness fire+clear all confirmed.
- (earlier, main, PR #1 by another agent) Windows port `shim/win/` + a real
  cross-platform bug fix: `os.kill(pid,0)` is DESTRUCTIVE on Windows, replaced
  with a non-destructive OpenProcess probe.
- (earlier, main, PR #2 by another agent) `docs/llm-drafter-design.md` — design
  for the stubbed `draft_update()`.

## Pending
- [ ] Commit this branch + open PR for config.toml
- [ ] Implement `draft_update()` per `docs/llm-drafter-design.md` — NOTE: its N1
      requires a shim-recursion guard (`AGENT_MEMORY_INTERNAL=1` + `--real-bin`)
      that the shim does NOT yet honor. Don't ship draft_update without it.
- [ ] Decide draft cadence: doc defaults to "continuous" — I'd challenge that
      (spends the user's own agent budget); prefer exit-only/quiescence default.
- [ ] Surface staleness in the terminal, not just the log (design fork: shell
      hook vs `human-memory status` command)
- [ ] Polling → native inotify (Unix) / ReadDirectoryChangesW (Win)
- [ ] Resolution override / multi-agent config wiring
- [ ] Code-sign Windows scripts (durable AV false-positive fix)

## Key Decisions
- **config.toml errors are loud-but-safe**: log the problem, use defaults. A
  silent bad config would masquerade as "defaults are fine."
- **Example == defaults invariant**: a test asserts `config.toml.example` parses
  to the built-in `Config()`, so docs can't drift from code.
- **Installers never clobber an existing config.toml** — user tuning survives re-install.
- (project-wide) PATH+exec shim is the only universal interception layer across a
  native binary (claude), Node (codex), and a Bun exe (opencode).
- (Windows) no `exec()`: shim stays parent in the same console; watcher tracks its PID.

## Where I Left Off
`shim/watcher.py` — `load_config()` (top) + `main()` loop now use a `Config`
dataclass. Next: commit `feat/config-toml`, push, open PR. After that, the
highest-value thread is `draft_update()`, but it's blocked on the N1 shim-guard
and the cadence decision above — resolve those first.

> Note: this whiteboard was rewritten as a *union* of all agents' work. PR #1's
> entry had overwritten it to read as if the Windows port were the whole project
> — exactly the clobbering failure this project exists to fix. Open question for
> the project itself: how should HUMAN_MEMORY.md merge across parallel agents?

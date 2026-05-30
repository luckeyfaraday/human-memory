# HUMAN_MEMORY.md

## Current State
Initial prototype of the transparent shim + freshness watcher is built and
verified. Repo bootstrapped from design phase to working PoC.

## What Just Happened
- Explored env: `claude` is a native ELF, `codex` is Node, `opencode` is Bun —
  no common runtime, which is *why* the PATH-shim approach is the right layer.
- Wrote `README.md` (vision, architecture, rollout plan, limitations).
- Built `shim/agent-shim` (busybox-style transparent shim, exec handoff),
  `shim/watcher.py` (polling freshness engine), `shim/install.sh`.
- Verified end-to-end in a sandbox: resolution skips the shim's own dir,
  args/exit-code pass through transparently, watcher spawns + exits with agent,
  staleness detection fires and clears correctly.

## Pending
- [ ] `config.toml` parsing (thresholds/ignore-globs are constants for now)
- [ ] Upgrade watcher from polling → native inotify
- [ ] Implement `draft_update()` — currently stubbed (warn-only)
- [ ] Surface staleness in the terminal, not just the log (passive dashboard)
- [ ] Resolution override / multi-agent config wiring

## Key Decisions
- **PATH shim over plugin/hook**: only universal interception point across a
  native binary, a Node script, and a Bun exe.
- **`exec` handoff, not PTY proxy**: guarantees TTY/signal/exit transparency;
  observation is filesystem-side only (shim stays out of the data path).
- **Detached watcher tracks the shim's PID**: outlives the exec, dies with the agent.
- **Staleness is relative** (work advancing while memory is frozen), not absolute age.

## Where I Left Off
Prototype complete and committed. Next concrete step: `shim/watcher.py` —
replace the constant thresholds with a `config.toml` loader, then implement the
stubbed `draft_update()` near the `STALE:` log emission (search "stubbed").

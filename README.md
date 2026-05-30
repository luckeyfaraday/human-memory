# human-memory

**Agent memory got good. Human memory didn't.**

When you run a swarm of coding agents — Claude, Codex, OpenCode, Hermes — at once,
you lose the thread. What's pending? What got decided? Where did each agent leave
off? The agents remember their own context perfectly. *You* are the one paging it
all in and out of a single working memory, and you're the bottleneck.

`human-memory` is infrastructure for **your** memory, not the agent's.

---

## The artifact: `HUMAN_MEMORY.md`

A live state file each agent keeps current as it works. Not a report you read after
the fact — a running whiteboard you glance at to reload context in ~10 seconds.

```markdown
# HUMAN_MEMORY.md

## Current State
Refactoring the auth middleware. Mid-edit in `server/auth.ts`.

## What Just Happened
- Extracted `verifyToken()` into its own module
- Tests pass except `expired-token.test.ts` (failing, see Pending)

## Pending
- [ ] Fix expired-token test — clock mock isn't advancing
- [ ] Wire new module into `app.ts`
- [ ] Update API docs

## Key Decisions
- Using `jose` over `jsonwebtoken` — already a dep, smaller, ESM-native
- Tokens verified at middleware layer, not per-route (DRY)

## Where I Left Off
`server/auth.ts:142` — half-written `catch` block for the expired-token path.
Next: make the test's clock mock advance past `exp`.
```

Five sections, fixed order. The point is **scannability**: same shape every time, so
your eye knows where to land.

---

## The hard part: keeping it fresh

Prompt conventions alone are weak. "Please update HUMAN_MEMORY.md as you go" works for
a while, then the agent drifts — gets absorbed in the task and forgets the meta-task.
A stale whiteboard is worse than none, because you *trust* it.

So the freshness mechanism can't depend on the agent's goodwill. It has to live
**outside** the agent, watching.

---

## Product direction: the transparent shim

The core idea: intercept the agent at the one place every invocation passes through —
the command you type to launch it.

- You still type `claude`, `codex`, `opencode`. Nothing changes for you.
- A lightweight shim sits **earlier in `PATH`** (e.g. `~/.agent-memory/bin/claude`).
- It forwards everything to the real binary unchanged — same args, same TTY, same
  colors, same exit code. Transparent.
- *Meanwhile*, in the background, it watches the working directory for file changes,
  compares their timestamps against `HUMAN_MEMORY.md`, and flags or drafts an update
  when the whiteboard goes stale relative to the work.

Zero workflow change. Memory maintenance happens underneath the commands you already
run.

### Why a PATH shim and not a wrapper script / plugin / hook?

The three target agents are deliberately heterogeneous:

| Agent      | What it actually is                          |
|------------|----------------------------------------------|
| `claude`   | compiled native ELF binary                   |
| `codex`    | Node.js script (`codex.js`)                  |
| `opencode` | Bun-compiled executable (`opencode.exe`)     |

There is no shared plugin system, no common runtime to hook. The **only** universal
interception point is the process boundary: `PATH` resolution + `exec`. That's the
layer the shim operates at, which is exactly why it works across all three (and
anything added later) without per-agent integration.

---

## Architecture

### 1. Resolution & launch (the shim itself)

```
        you type: claude --resume
                  │
                  ▼
   ~/.agent-memory/bin/claude   ◀── earliest in PATH; one file, busybox-style:
        (generic shim)              behaves as whichever name it's invoked under ($0)
                  │
        ┌─────────┴──────────┐
        │                    │
        ▼                    ▼
  spawn watcher        resolve REAL binary
  (background,         (scan PATH, skip our
   detached)           own dir by realpath,
        │              dedupe, pick first match)
        │                    │
        │                    ▼
        │              exec real binary with all args
        │              (process is replaced → perfect
        │               TTY / signal / exit transparency)
        ▼
  watch cwd for file mods, track HUMAN_MEMORY.md
  freshness; warn / draft when stale; exit when
  the agent process exits
```

**Key design choices**

- **`exec`, not fork-and-proxy.** The shim `exec`s the real binary so the shim process
  is *replaced*. The terminal talks directly to the real agent — raw mode, `SIGWINCH`
  resize, ANSI, ^C all behave natively. No PTY proxying, which is the thing most likely
  to subtly break a TUI.
- **Observation lives in a separate detached process.** Because `exec` replaces the
  shim, it can't do post-launch work itself. So *before* exec'ing, the shim spawns a
  background watcher that outlives the handoff and monitors the filesystem
  independently. The watcher tracks the agent's PID and exits when the agent does.
- **Real-binary resolution must be robust.** Walk `PATH` left to right, `realpath` each
  candidate, skip any that resolve into our shim dir (avoids infinite self-exec), and
  dedupe (the live `PATH` here has `~/.local/bin` listed multiple times). A configured
  absolute-path override wins if present.
- **One shim, many names.** A single `agent-shim` file; `claude`/`codex`/`opencode`
  are symlinks to it. It reads `$0`'s basename to know who to impersonate. Adding a new
  agent = one symlink.

### 2. The watcher (freshness engine)

MVP: a polling loop (no `inotify*` tools on this box; native inotify is a later
upgrade). Each tick it:

1. Scans the working tree for files modified since last tick (ignoring `.git`,
   `node_modules`, `HUMAN_MEMORY.md` itself).
2. Reads `HUMAN_MEMORY.md`'s mtime.
3. If real work has happened but the whiteboard hasn't moved in `N` edits / `T`
   seconds → **stale**.
4. On stale: emit a warning (and later, draft a suggested update from the diff).

Staleness is about *relative* movement — work advancing while the memory stands still —
not absolute time.

### Layout

```
~/.agent-memory/
  bin/
    agent-shim        # the one generic shim (this repo: shim/agent-shim)
    claude  -> agent-shim
    codex   -> agent-shim
    opencode-> agent-shim
    human-memory      # the viewer (this repo: shim/human-memory)
  lib/
    watcher.py        # freshness engine (this repo: shim/watcher.py)
  config.toml         # binary overrides, thresholds, ignore globs
  log/                # watcher logs, per-session
  state/              # live per-session status the viewer reads (auto-cleaned)
```

---

## Rollout path

1. **Convention** — lock the `HUMAN_MEMORY.md` format; add it to agent configs/prompts.
   *(format defined above)*
2. **Passive dashboard** — staleness detection + a memory viewer. *(watcher + `human-memory`, this repo)*
3. **Transparent shim** — invisible PATH interception. *(shim prototype, this repo)*
4. **Optional enforcement** — team mode that can block/nag on chronically stale memory.

---

## Prototype status

This repo currently contains a **working proof-of-concept** of stages 2–3:

- `shim/agent-shim` — the generic, busybox-style transparent shim (bash; Unix
  + Git Bash / WSL).
- `shim/watcher.py` — polling freshness engine (Python 3, stdlib only;
  cross-platform — Windows liveness uses a non-destructive handle probe, see below).
- `shim/human-memory` — the viewer: `human-memory` shows a live status table of
  every running agent and whether each one's whiteboard is stale; `human-memory show`
  prints a whiteboard; `human-memory nag` is a one-liner for a shell prompt hook.
- `shim/install.sh` — sets up `~/.agent-memory/bin`, symlinks, and prints the
  one `PATH` line to add to your shell rc.
- `shim/win/` — the Windows port: `agent-shim.ps1` (PowerShell engine),
  `agent-shim.cmd` (cmd.exe companion), and `install.ps1`. Same model, adapted
  to a platform with no `exec()` (see *Windows* below).
- `HUMAN_MEMORY.md` — this project dogfooding its own format.

### Try it

```bash
shim/install.sh                      # creates ~/.agent-memory, symlinks, prints PATH line
# add the printed line to ~/.bashrc, then open a new shell
claude                               # runs the REAL claude; watcher spins up underneath
human-memory                         # status table of every running agent + staleness
human-memory show                    # print the current dir's HUMAN_MEMORY.md
```

`human-memory` reads each watcher's live status, so a stale whiteboard reaches your eyes
instead of dying in a logfile. Example:

```
AGENT   PID     STATE  BEHIND  AGE  DIR
claude  48213   STALE  6       12s  ~/work/api
codex   48230   fresh  0       3s   ~/work/web

1 session(s) STALE — their HUMAN_MEMORY.md is behind the work.
```

Optional — get nagged automatically at your shell prompt when the current dir falls
behind (the installer prints this line):

```bash
PROMPT_COMMAND='human-memory nag; '$PROMPT_COMMAND
```

To uninstall: remove the `PATH` line and `rm -rf ~/.agent-memory`.

### Windows (PowerShell + cmd.exe)

Windows has no `exec()`, so the shim can't *replace* itself with the real agent.
Instead `shim/win/agent-shim.ps1` stays alive as the parent, runs the real agent
**in the same console** (so the TUI, ANSI, raw mode and Ctrl+C are all native),
and propagates its exit code. The watcher is launched with `pythonw` (no console
window) and tracks the shim's PID, exiting when the agent session ends. Which
shim wins depends on the shell — PowerShell prefers `claude.ps1`, cmd.exe prefers
`claude.cmd` — so both are installed.

```powershell
powershell -ExecutionPolicy RemoteSigned -File shim\win\install.ps1   # copies into ~\.agent-memory, prints the PATH line
# Put the printed line earliest on PATH (it prints a session command and an
# optional persistent one — it does NOT edit your environment for you), then:
claude --version                     # runs the REAL claude; watcher spins up underneath
Get-Content $HOME\.agent-memory\log\*.log
```

To uninstall: remove that `PATH` entry and `Remove-Item -Recurse $HOME\.agent-memory`.

### Antivirus / SmartScreen

Be aware: a transparent PATH shim that intercepts commands and runs a hidden
background watcher is, *behaviorally*, indistinguishable from a PATH-hijacking
trojan — so heuristic AV (and AMSI on Windows) may flag it. This is a real
false-positive risk for end users, not just a theoretical one. The Windows port
is deliberately built to minimize the signal:

- launches the watcher with **`pythonw`** (no window) rather than
  `-WindowStyle Hidden`, which AV weights heavily;
- the `.cmd` uses **`-ExecutionPolicy RemoteSigned`**, not `Bypass`;
- `install.ps1` runs **`Unblock-File`** on the installed copies so they're
  trusted-local.

The durable fix is **code-signing** the scripts; until then, users may need to
add an exclusion. (Note: invoking the shim from inside another tool's
`powershell -ExecutionPolicy Bypass -Command …` wrapper can still trip
"PowerShell spawning PowerShell" heuristics — that's the wrapper, not the shim.)

### Configuration

Tunables live in `~/.agent-memory/config.toml` (seeded from `shim/config.toml.example`
on install — re-installing never clobbers your edits). Every key is optional and falls
back to a built-in default:

```toml
[watcher]
poll_seconds = 5              # how often the watcher scans the tree
stale_edit_threshold = 8      # nag after this many edits since the whiteboard moved
stale_seconds_threshold = 180 # ...or this long with work advancing and memory frozen
ignore_dirs = [".git", "node_modules", "..."]   # dirs skipped while scanning
ignore_suffixes = [".pyc", ".log", ".swp", ".tmp"]
```

A missing, malformed, or partially-bad config never crashes the watcher — it logs what
went wrong and falls back to defaults (config problems are reported, not silent).

### Known limitations (MVP)

- Watcher polls instead of using `inotify` — fine for a prototype, will burn a little
  idle CPU. Native inotify is the planned upgrade.
- "Draft an update from the diff" is stubbed — currently warns only. Design recorded in
  `docs/llm-drafter-design.md`.
- Resolution override / multi-agent config not yet wired.

---

## Non-goals

- Replacing the agent's own memory. This is orthogonal — it's *your* dashboard.
- Capturing or proxying agent I/O. The shim deliberately stays out of the data path
  (`exec` handoff) to guarantee transparency. Observation is filesystem-side only.

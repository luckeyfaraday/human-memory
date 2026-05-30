# LLM-backed `draft_update()` — design decisions to resolve

**Status:** Open — needs decisions before implementation.
**Last updated:** 2026-05-30
**Owners:** @luckeyfaraday (+ whoever picks this up next)

This doc parks an in-progress design discussion so it survives a machine switch or
a handoff. It records (a) what the feature is, (b) what we've already decided, and
(c) the **open questions that must be answered before we write code**. Each open
question lists the options, the trade-offs, and a recommendation — but the decision
line is left as `DECISION: TBD` on purpose. Fill those in, then implement.

---

## Background — the gap we're closing

The shim + `watcher.py` today **detect** that `HUMAN_MEMORY.md` has gone stale, but
they don't **act**: the only thing the watcher does on staleness is append a warning
to a log file. The function that would actually keep the whiteboard populated —
`draft_update()` in `shim/watcher.py` — is a commented-out stub (see lines ~187–188
and the README *Known limitations*).

So out of the box, nothing writes `HUMAN_MEMORY.md`. The README's whole thesis is
that prompt conventions drift and freshness must be enforced **outside** the agent —
but the enforcement action was never built. This feature builds it: the watcher
itself authors the whiteboard from the changes it observes, so it never depends on
the agent (or the human) remembering to.

Crucial constraint that shapes everything: the shim deliberately stays **out of the
agent's I/O path** (`exec`/hand-off, never proxy — it's a stated non-goal). So we
*cannot* make the agent write the file by injecting a prompt. The only way to escape
"agent goodwill" is for the **watcher** to write it. That's why this is a watcher
feature, not a prompt change.

---

## Decided so far

These are settled and don't need re-litigating (but are recorded for the cold reader):

- **D1 — Backend = headless agent (default).** The watcher shells out to the *same*
  agent the user already launched (`claude -p`, `codex exec`, `opencode run` — exact
  flags TBD per agent), reusing their existing auth. Chosen over a direct API or a
  local model because it requires **zero new setup**, adds **no dependency**, and
  keeps the **privacy boundary unchanged** (a `codex` user's diffs shouldn't suddenly
  route to Anthropic). See the comparison table in the appendix. The backend lives
  behind a small `Summarizer` interface so `api` / `local` can be swapped in later.
- **D2 — Cadence = continuous, not exit-only.** The whiteboard updates live *while*
  the agent works (drafting on each staleness event), so you can watch context
  accumulate in real time. A final draft on exit is fine too, but continuous is the
  on-vision behavior; exit-only is not enough.
- **D3 — Drafts go to a sidecar, not straight onto the live file.** A wrong
  auto-generated whiteboard is worse than a stale one ("you trust it"). So drafting
  writes to a sidecar (e.g. `HUMAN_MEMORY.md.draft`) and something promotes it to the
  live `HUMAN_MEMORY.md` separately. *How/when to promote is OPEN — see Q1.*

---

## OPEN QUESTIONS — resolve these before implementing

### Q1 — Promotion model: how does the sidecar become the live file?

We agreed drafts land in a sidecar (D3). The unsolved part is **when do we overwrite
the real, trusted `HUMAN_MEMORY.md`?**

- **Option A — Quiescence-promote (recommended).** The sidecar churns continuously as
  the agent works; we promote it to the live file when **work has been idle for Q
  seconds** (e.g. 20–30s of no new edits) and **always on agent exit**. Rationale:
  filesystem-idle is a clean signal that a chunk of work just finished — a coherent
  checkpoint, which is exactly when a human would glance at the whiteboard. The live
  file updates at settled checkpoints instead of flickering mid-edit. Back up the
  live file before each promote (`HUMAN_MEMORY.md.bak` and/or git) so a bad draft is
  one revert away.
- **Option B — Live-write-with-backup.** Skip promotion entirely: write drafts
  straight to the live file continuously, and treat the *sidecar* as the **backup of
  the last-trusted version**. Always-freshest, simpler (no promotion logic), but you
  may catch the whiteboard mid-thought / in a raw state.

Trade-off in one line: **A = "settled checkpoints, trustworthy per glance"** vs
**B = "always freshest, sometimes raw, dead simple."**

Either way we need: a backup of the prior good version, and a manual override
(e.g. `touch .human-memory-promote` to force now, `.human-memory-hold` to freeze the
live file while reading).

**DECISION: TBD**

---

### Q2 — Drafter model: cheap/fast or full quality?

Continuous background drafting runs through the user's *own* agent, so it **shares
their usage budget and rate limits with the foreground session they're actively
running** — it can throttle the work they actually care about.

- **Option A — Pin the drafter to a cheap, fast model (recommended).** e.g.
  `claude -p --model haiku`. The task is "summarize a diff into five fixed sections" —
  it doesn't need frontier reasoning, and this keeps continuous drafting from eating
  their Opus/Sonnet quota. Per-agent flag for model selection is TBD.
- **Option B — Use the full model.** Best summary quality, but every draft competes
  with the foreground session for budget and rate limits. Likely painful under
  "continuous."

**DECISION: TBD**

---

## Design notes already agreed (not open, but must be honored in implementation)

- **N1 — Shim-recursion guard.** The watcher wants to call `claude`, but `claude` is
  *shimmed* → naive shell-out re-enters the shim → spawns another watcher → loop.
  Mitigation: call the **real resolved binary** (the shim already computed it; pass it
  down as `--real-bin`) **and** set `AGENT_MEMORY_INTERNAL=1` so any accidental shim
  hit refuses to spawn a nested watcher.
- **N2 — Trigger reuses existing staleness machinery.** Don't draft every poll. The
  watcher already computes "work advanced while memory froze" — that *is* the draft
  trigger. Draft on that event, with thresholds tunable down for "continuous."
- **N3 — Incremental, single-flight.** Each draft feeds the LLM *previous sidecar +
  diff since last draft* so it **amends** rather than regenerates (cheaper, stable
  wording). A lock ensures one draft at a time; overlapping events coalesce.
- **N4 — Input = git diff when available.** Prefer `git diff` as summarization input
  in a git repo; fall back to the mtime-based changed-file list otherwise.
- **N5 — Best-effort, hard timeout.** The drafter subprocess gets a timeout and
  swallows all errors — it must never hang or break the agent (matches existing
  watcher ethos).
- **N6 — AV hygiene (Windows).** Spawn `pythonw → real-agent.exe` directly. Do **not**
  wrap the call in `powershell -Command` — that re-triggers the `PowhidSubExec`
  "PowerShell spawning PowerShell / hidden subprocess" heuristic. See the AV notes in
  the README and the project memory.

---

## Proposed loop (for reference once Q1/Q2 are decided)

Assuming Q1=A (quiescence) and Q2=A (cheap model):

```
poll (every POLL_SECONDS):
  if work advanced since last poll: accumulate "dirty"
  if dirty >= DRAFT_THRESHOLD and no draft in flight:
      DRAFT: real-agent -p --model <cheap>
             input = prev sidecar + git diff
             output -> HUMAN_MEMORY.md.draft
  if sidecar newer than live and work idle >= Q seconds:
      PROMOTE: backup live -> .bak, copy sidecar -> live

on agent exit:
  final DRAFT if dirty, then PROMOTE
```

All new state piggybacks on what the watcher already tracks; the only new external
action is the drafter subprocess (guarded per N1/N6).

---

## Appendix — backend comparison (why D1)

| | Headless agent (`claude -p`, `codex exec`, `opencode run`) | Direct API | Local model (Ollama) |
|---|---|---|---|
| New setup for user | **None** — reuses existing auth | API key + billing | Install + run a model server |
| New dependency | None (subprocess) | None if hand-rolled `urllib` | Ollama runtime |
| Privacy boundary | **Unchanged** — same provider they chose | Shifts (codex user's code → Anthropic?) | **Best** — never leaves machine |
| Offline | No | No | **Yes** |
| Summary quality | High (frontier) | High | Lower; 5-section discipline suffers |
| Marginal cost | Their existing sub | Pay-per-token, separate | Free |
| Resource contention | Light (infrequent) | Light | **Heavy** — competes for GPU/RAM |
| Agent-agnostic | **Yes by construction** | Picks one provider for all | Neutral |

Decisive column: setup friction. Headless agent is the only option that asks the
user to configure **nothing**, which is the whole "zero workflow change" promise.

---
id: M2-02
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
status: RESOLVED
resolved: 2026-07-27
---

# M2-02 — Orchestrator: final pick, and is it even load-bearing?

## Verdict

**Not load-bearing. Adopt no orchestrator codebase. Conform to `SPEC.md` where the
semantics fit, and take two specific mechanisms from it.**

The ticket's own reframing turned out to be the answer: *"the less the
orchestrator does, the less its identity matters."* After W-06 split execution
into two substrates and M2-07 gave L3 to Open SWE, what is left for an
orchestrator to own is poll, claim, dispatch, reconcile — a few hundred lines of
state machine whose hard parts (durability, circuit breaking, cost caps) are
precisely the parts **no candidate implements**.

Picking one would mean adopting a dependency for the easy half of a problem and
still building the hard half.

## Why each candidate is out

**OpenSymphony — out. It stopped being an orchestrator.**
Now 16 crates: `planning`, `memory`, `code-intel`, `gateway`, `openhands`,
`control`, a TUI. `crates/opensymphony-gateway/src/lib.rs` is **235 KB in a single
file**. The earlier assessment ("port the memory-bucket design") was made when
this was a readable Rust orchestrator; it is now a platform, and adopting it means
adopting a planning system and a memory system nothing here asked for. Retained as
a reading exercise for the memory-bucket design only.

**Kata — out, and its distinguishing feature is now upstream.**
SSH worker pools were the reason to keep reading it. `SPEC.md` **Appendix A** now
specifies that extension directly — per-host caps, prefer-previous-host on retry,
and explicit failover semantics. There is nothing left that Kata has and the spec
does not.

**Baton — remains eliminated** (recorded in the original ticket; the Python-seam
argument did not survive scrutiny).

**Open SWE — covers L3, not this.** M2-07 stands: it is a coding agent, not a
supervised job runner. It does not own the experiment queue (M2-03), a durable
store, a cross-job breaker, or GPU cost caps.

## What to take from `SPEC.md`

Two mechanisms, both already specified, both cheaper than the prose they replace.

### §8.3 — per-state concurrency limits

    max_concurrent_agents_by_state[state], else the global limit

MAP.md's founding constraint — *"any design that raises agent concurrency without
raising review capacity is rejected by default"* — has been prose since day one.
A per-state cap on the human-review state is that constraint as configuration:
the queue in front of a human is bounded and dispatch stalls rather than piling
up. W-11 ladder rung: config, not prose. Take it.

### Appendix A — the SSH worker extension

The external-compute path C-01 deferred ("local GPU now, edge or external
later"), already specified: hosts as a pool, per-host caps, and the rule that
matters —

> When all SSH hosts are at capacity, dispatch SHOULD wait rather than silently
> falling back to a different execution mode.

Silent fallback to a different substrate would make a run's provenance a guess.
Adopt the semantics when compute leaves this machine.

## Where the spec does *not* apply, and why

**§11.5 — tracker writes.** The spec says the coding agent performs ticket
mutations through provider-native tools, and *"the service remains a
scheduler/runner and tracker reader."*

**We do the opposite, deliberately.** The runner adjudicates and writes the
verdict; the agent returns evidence (invariant 9). Symphony's boundary is correct
under Symphony's threat model — when CI is the arbiter, the agent cannot fake a
green suite, so letting it move the ticket is safe. Here the deliverable **is**
the number, so the actor that produces it cannot be the actor that records it.

Recorded as a deliberate non-conformance rather than an oversight, so a later
reader does not "fix" it toward the spec.

**§13.5 — token accounting.** Specified thoroughly and enforces nothing:
accumulate, track, report. W-12's caps have no upstream equivalent.

**§14.3 — in-memory scheduler state, no restart recovery.** By design, and the
reason M2-03 exists.

## Consequence

M2-02 closes without a dependency. The runner in `runner.py` stays ours; §8 and
§16 become the reference it conforms to where the semantics fit. The three
Symphony breakages W-08 identified — no durable state, no circuit breaker, stall
timeout tuned for chatty agents rather than silent compute — remain ours to fix,
which was always the case and is now confirmed against the current spec rather
than a summary of it.

Full survey: `docs/research/agent-factories-2026.md`.

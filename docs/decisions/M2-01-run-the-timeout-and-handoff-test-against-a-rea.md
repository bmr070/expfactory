---
id: M2-01
parent: wayfinder:map2
labels: [wayfinder:task]
mode: HITL
blocked-by: []
assignee:
status: open
---

# M2-01 — Run the timeout and handoff test against a real orchestrator

## Question

Settle the single biggest unknown empirically instead of from specs.

Three steps, one day:
1. `cargo install opensymphony`; `opensymphony init` against a scratch repo; drive one trivial
   deterministic ticket end to end. Does the loop work on your machine at all?
2. Configure a lifecycle hook that sleeps 20 minutes. Does the orchestrator kill it or wait? SPEC.md
   documents a 5-minute stall timeout and a 1-hour turn timeout, but the observed behaviour for a
   silent compute-bound hook is what matters.
3. Have `after_run` shell out to the Python gate harness on a fixture from the adversarial suite.
   Does the artifact handoff work across the process boundary?

Records facts, not opinions. Step 2 may collapse two later tickets: if long hooks survive, a separate
queue may be unnecessary; if they are killed, the split is confirmed and the queue becomes required.

---
id: M2-04
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
blocked-by: []
assignee:
status: open
---

# M2-04 — How do you preregister an inherently exploratory hill-climb?

## Question

Design the preregistration contract before building the gate.

The failure being closed: an agent runs an experiment, sees the primary metric didn't move but
latency improved, and reports latency as the win. Every existing gate passes — leakage, seed
variance, holdout budget and tamper detection are all silent on a metric chosen after the fact.
Named in the literature as HARKing and S-hacking.

The tension: preregistration assumes a confirmatory study; a hill-climb is exploration. Literature
suggests preregistering the *selection methodology* rather than a specific hypothesis, but that is a
sketch.

Resolve: what exactly is declared before a run (primary metric? metric + direction + minimum effect?
the search space? the tuning algorithm?), what is allowed to change mid-climb and by whom, how
exploratory runs are marked so they cannot be promoted, and whether secondary metrics may ever
justify promotion (proposed: no, recorded but never sufficient).

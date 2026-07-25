---
id: M2-05
parent: wayfinder:map2
labels: [wayfinder:prototype]
mode: HITL
blocked-by: [M2-04]
assignee:
status: open
---

# M2-05 — Build the preregistration gate and its fixtures

## Question

Implement whatever W-04 settles, red-green, as a seventh L0 gate.

Must include: a declared-metric field on the ticket/candidate, that declaration recorded in the
ledger row, a gate blocking promotion when the reported metric differs from the declared one, and
fixtures in the adversarial suite — at minimum one metric-shopped candidate (expect REJECT) and one
honest candidate reporting a declared metric that moved (expect PROMOTE), plus a held-out variant.

Standing rule from Map I: every gate traces to a fixture.

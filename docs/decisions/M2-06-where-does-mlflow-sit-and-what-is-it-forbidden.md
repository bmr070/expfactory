---
id: M2-06
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
blocked-by: [M2-03]
assignee:
status: open
---

# M2-06 — Where does MLflow sit, and what is it forbidden from doing?

## Question

Place the observability layer without letting it become an adjudicator.

Established: MLflow for tracing, never for adjudication — the ledger keeps the verdict, and a green
dashboard line is never a promotion signal. What is unresolved is the mechanics: does the queue write
to MLflow and the ledger separately (two sources of truth, drift risk), does the ledger row reference
an MLflow run id (one truth, one index), or does MLflow read from the ledger?

Also resolve what MLflow is structurally prevented from doing, so the boundary is enforced rather
than merely intended.

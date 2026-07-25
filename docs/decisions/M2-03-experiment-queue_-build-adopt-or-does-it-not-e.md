---
id: M2-03
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
blocked-by: [M2-01]
assignee:
status: open
---

# M2-03 — Experiment queue: build, adopt, or does it not exist?

## Question

Decide what owns the hours-long half of the work: accept a job, run it on the GPU substrate,
collect the artifact, run gates, append to the ledger.

Candidates: Prefect (general-purpose, Python-native, bring your own tracking), Metaflow
(ML-experiment-first, built-in artifact versioning — may duplicate the ledger), Celery/RQ (plain job
queue, no ML opinions), a file-backed queue (~200 lines, zero dependencies), or nothing if W-01 shows
long hooks survive.

Weigh: the ledger already IS the tracking layer, which may make Metaflow's headline feature redundant
and its opinions costly. And orchestrator choice is sticky — a wrong pick here is expensive to undo.

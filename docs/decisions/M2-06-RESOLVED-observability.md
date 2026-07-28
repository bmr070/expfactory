---
id: M2-06
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
status: RESOLVED
resolved: 2026-07-27
---

# M2-06 — Where does MLflow sit, and what is it forbidden from doing?

## Verdict

**The ledger row carries an opaque tracker reference. Nothing flows the other
way, and the adjudicating modules cannot import a tracking client at all.**

Of the three mechanics the ticket named:

| Option | Verdict |
|---|---|
| Queue writes to both separately | **No.** Two sources of truth, and drift between them is undetectable — each looks internally consistent. |
| Ledger row references a tracker run id | **Yes.** One truth, one index. |
| Tracker reads from the ledger | Fine, additive, and not needed yet. |

## Why the reference points that way

Direction decides who can break whom.

A ledger row holding a tracker run id has a dependency that can dangle — wipe the
tracking store and the id resolves to nothing. **The ledger row is still valid**,
because the id was never load-bearing: it is an index into a place you can go
look at curves, not an input to any decision.

Reverse it and the property is lost. A tracker holding the authoritative record
of a verdict means the verdict lives in a mutable external store that nothing in
this repo controls, with no append-only guarantee and a UI that invites editing.

So the rule is about what the reference *is*, not just which way it points:

> **The ledger never parses the reference.** It is an opaque string. If the
> ledger cannot interpret it, the tracker cannot feed it anything meaningful.

That is the same move as `native_ref` in Symphony's tracker adapter (§4.2) —
carry the provider's richness without letting the scheduler depend on its shape.

## What it is structurally prevented from doing

The ticket asked for a boundary that is *enforced rather than merely intended*.
Prose does not ratchet (invariant 8), so:

`tests/test_observability_boundary.py` parses each adjudicating module and
asserts none of them imports `mlflow`, `wandb`, `tensorboard`, `comet_ml`,
`neptune`, `langsmith` or `clearml`. Nine modules, checked by import rather than
by text search — what matters is whether the code can *reach* the tracker, and a
substring match would fire on any comment mentioning it, which is how a firewall
test becomes something people skim.

Three properties make it a wall rather than a note:

- **It fires.** `test_the_check_would_actually_fire` feeds it a module that does
  the forbidden thing and requires an objection. A firewall test that cannot
  fail is decoration.
- **It catches the nested form.** `import mlflow.tracking` and
  `from mlflow.entities import Run` both reach mlflow; comparing full dotted
  paths against a flat set would miss both.
- **It knows when it is protecting nothing.** A module renamed or added without
  being listed is unprotected while the file still reads as protection — the
  failure CODEOWNERS names in its own header. A second test asserts every listed
  module exists.

The failure this prevents is not someone deliberately gating on a dashboard. It
is the ordinary version: a metric read back from the tracker *"just to compare"*,
which quietly makes a verdict depend on a mutable store.

## Adoption is deferred, and the boundary is not

Nothing is installed. C-01 put compute on one local card, runs are minutes to
hours, and the ledger already records everything that adjudicates — so a tracking
service earns its keep only when there are enough runs that curves beat rows.

The placement is settled **now** rather than when someone installs it, because
that is the moment the shortcut is tempting and the decision would get made by
whoever is holding the keyboard. Same shape as W-12's inference cap: specify the
boundary, do not stub the mechanism.

When it does land: `SANDBOX_TYPE`-style explicit configuration, no inherited
defaults (the M2-07 action), and the reference goes on the ledger row as a
string nothing reads.

## Note on LangSmith

M2-07 established that adopting Open SWE does not force LangSmith, and that even
if used it occupies agent-session tracing while the ledger adjudicates. It is on
the forbidden-import list above for the same reason as MLflow: the argument that
"it is only tracing" is a statement about intent, and this file is about what the
code can reach.

## Refs

`tests/test_observability_boundary.py`, `docs/decisions/M2-03-RESOLVED-experiment-queue.md`,
`docs/decisions/M2-07-RESOLVED-open-swe.md`, `docs/decisions/W-12-RESOLVED-cost-caps.md`.

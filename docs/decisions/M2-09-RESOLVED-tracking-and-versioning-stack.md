---
id: M2-09
parent: decisions/M2-06-RESOLVED-observability.md
labels: [wayfinder:decision]
mode: HITL
status: RESOLVED
resolved: 2026-07-30
ticket: BRE-8
---

# M2-09 — Experiment tracking and data/model versioning: which, and does it exist yet?

## The ask

BRE-8 (ticket 003): *"MLflow (self-host) vs HF Trackio for tracking; DVC + HF Hub
for data/model versioning [...] Recommendation: MLflow + HF Hub + DVC for v1."*

M2-06 already settled **where** a tracker sits and what it is forbidden from
doing. It did not pick one, and it said nothing about data or model versioning.
Those are the two open halves.

## Verdict

| Layer | v1 | Why |
|---|---|---|
| Experiment tracking | **Trackio when one is wanted. Not MLflow.** Nothing installed yet. | Local-first matches C-01; there is no server to run and no second service to keep alive |
| Data versioning | **Nothing new.** Git commit SHA + content hash of the split. | Already built, and strictly stronger for the question that actually decides promotions |
| Model versioning | **Nothing new** at v1. HF Hub named as the trigger-gated next step | Nothing is distributing weights yet, and a registry with one consumer is a filing cabinet |

The ticket's own recommendation is overturned on two of three. That is the
interesting part of this decision, so it is argued below rather than asserted.

## Tracking: Trackio over MLflow

Both satisfy M2-06's rule identically, because M2-06's rule is about *direction*
— the ledger row carries an opaque reference, the adjudicating modules cannot
import a tracking client — and neither product can violate a rule enforced by
`tests/test_observability_boundary.py`. So the choice is on operational cost, and
there Trackio wins on the specific facts of this project:

- **Local-first by default.** Experiments persist locally with no server;
  MLflow self-hosted means a tracking server and a backing store to keep alive
  for a factory that runs one workload on one card (C-01). That is a daemon whose
  only job is to draw curves.
- **A drop-in `wandb` API** (`init` / `log` / `finish`), so the adoption cost is
  an import line and the *removal* cost is the same. M2-06 deferred adoption
  deliberately; a tracker that is cheap to remove keeps that deferral honest.
- **Optional Spaces sync**, which is the one thing MLflow's server was buying —
  a URL someone else can open — without running the server to get it.

**Where MLflow would win, and why it does not apply.** Its model registry with
stage transitions is a genuine capability Trackio has no equivalent for. It does
not apply because the registry's job — *this version is the approved one* — is
the ledger's job here, and installing a second thing that answers it is the
Metaflow argument from M2-03 word for word: not duplication, ambiguity about
which record is the truth.

**This is a preference, not a lock-in.** M2-06 already established that nothing
is installed until run volume makes curves beat rows. If that moment arrives and
Trackio has stalled, MLflow-as-a-dashboard is still admissible under the same
rule. What is *not* admissible is either of them holding a verdict.

## Data versioning: DVC is declined, and the reason is not cost

DVC is the recommendation in the ticket and it is the one worth arguing hardest,
because it is a good tool and the decline is not about it being bad.

**What is already built.** `drone_audio.py` pins the dataset by git commit SHA
(`DATASET_COMMIT = "1f1ffb21..."`), hand-provisioned, and `github.com` is
deliberately absent from the egress allowlist so nothing can fetch a different
one at run time. Each `RunResult` additionally carries `train_ids_hash` and
`eval_ids_hash` — a content hash of the **actual split membership**.

**Why that is stronger than a DVC pointer for this project's question.** DVC
versions *the files*. The thing that decides whether a promotion is real here is
whether the training and evaluation sets overlap, and at what grouping —
`DroneAudioDataset`'s 1,332 drone clips come from only 257 continuous recordings,
so a random split leaks by construction and G-09 exists to catch it. Two runs can
cite the identical DVC revision and still differ in the only respect that
matters. The split hash cannot: it is computed from what the run actually
trained and evaluated on.

So DVC would add a second provenance store that answers a *weaker* version of the
question the ledger already answers exactly. That is the Metaflow decline again,
and it is the third time the same argument has decided a tool choice in this
project — M2-03 (Metaflow), M2-06 (any tracker holding a verdict), and here.

**When DVC becomes right.** When a dataset stops being one pinned git repo:
multiple sources, an ELT step, or a snapshot too large for a hand-provisioned
directory. That is BRE-9's Tier-2 trigger and it is not met.

## Model versioning: deferred with a named trigger

Nothing distributes weights today. Checkpoints are local, gitignored, and
referenced by the ledger row that produced them.

**HF Hub is the right answer when the trigger fires**, and the trigger is
*someone other than this machine needs a specific checkpoint* — an edge board to
flash, a second machine, or a published result. Adopt it then, under M2-06's rule
unchanged: the Hub reference goes on the ledger row as an opaque string, and no
adjudicating module imports `huggingface_hub`.

Adopting it now would put a `repo_id` in a record that nothing reads, which is
how an unused field becomes a field someone starts trusting.

## The `factory eval report / compare` contract

BRE-8 requires the pick to satisfy the metrics-as-text contract (harness-spec
§3). It does, trivially and for the reason the contract was written: the report
is generated from the **ledger**, not from the tracker. A tracker that vanished
mid-flight would cost a dashboard and change no output. That property is the test
of whether this decision was implemented correctly, and it is the one to re-check
if a tracker ever does land.

## What this changes today

Nothing is installed, no dependency is added, and no module changes. The value is
that the next person to reach for DVC or stand up an MLflow server finds the
argument already made, with the trigger conditions written down — rather than
making it while holding the keyboard, which is the failure M2-06 named.

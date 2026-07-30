---
id: M2-10
parent: wayfinder:map2
labels: [wayfinder:decision]
mode: HITL
status: RESOLVED
resolved: 2026-07-30
tickets: [BRE-10, BRE-9]
---

# M2-10 — Capability model and infrastructure tiers: ratify what exists, name what does not

Two tickets, one decision, because they turn out to be the same shape: both ask
to ratify a tiered plan, and in both cases **tier 1 is already built and the
higher tiers have no trigger met**. Separating them would produce two documents
saying "not yet" for the same reason.

---

## Part 1 — BRE-10: the capability model

> *"Confirm Agent Skills (cross-vendor, progressive disclosure) + MCP
> (tool-search) + per-domain plugins as the competence model, with a
> repo-embedded self-extending skill library. Decide the v1 skill catalog [...]
> and the skill-authoring/review workflow."*

### Ratified: skills, embedded in the repo, versioned with the code

`.claude/skills/` holds seven, and the catalog is not the one the ticket proposed:

| Ticket proposed | What exists | |
|---|---|---|
| `pull-base-model` | — | not built; belongs to BRE-5's run, not to the factory |
| `run-eval` | `run-experiment` | submit to the substrate and **detach**, which is the part that is hard |
| `finetune/distill` | — | same as `pull-base-model` |
| `open-pr/self-review` | — | **deliberately absent, see below** |
| | `pull-ticket` | claiming work, and proving it is dispatch-eligible *first* |
| | `triage` | a finding becomes a ticket, in the right place, with a lane |
| | `hill-climb` | a preregistered attempt against a recorded baseline |
| | `eval-analysis` | reading a verdict without fooling yourself |
| | `add-gate` | a new check, with the fixtures that justify it |
| | `ratchet` | a recurring failure becomes the cheapest sufficient check |

The catalog diverged because the ticket's four are *task* skills and the seven
are *rule* skills. Each of the seven encodes a rule this repo already enforces
rather than teaching a procedure — which is the only version worth embedding,
because a procedure skill goes stale silently while a rule skill goes stale
loudly the moment the rule it cites changes.

**`open-pr/self-review` is not on the list and should not be.** Invariant 3 says
the reviewer runs in fresh context, and invariant 9 says the untrusted party
returns evidence and never a verdict. A skill named "self-review" is a name for
the thing both invariants forbid. The capability it was reaching for exists as
`review_fleet.py` (BRE-35), where the router decides *who* reviews and decides
nothing else.

### Ratified: MCP for tools, with the boundary already drawn

MCP is how this session reaches Linear and GitHub, and it is the right shape:
tools are discovered, not linked. The boundary that matters is already enforced
and is not an MCP question — `tests/test_observability_boundary.py` refuses any
adjudicating module that can *reach* a tracking or provenance client, however it
got there. A tool the agent can call is not a tool the verifier can call, and
that separation is structural rather than a policy about which servers to enable.

### Declined: per-domain plugins, for now

No plugin exists and none is needed. A plugin earns its keep when a capability
must be shared across repos; there is one repo. Revisit when BRE-36's
separate-repo-per-project intake actually has a second project in it — that is
the trigger, and it is not met.

### The authoring workflow is `ratchet`, and it already ran

The ticket asks for a skill-authoring and review workflow. It exists as the
`ratchet` skill, whose rule is invariant 8: a recurring failure becomes a lint
rule, hook, CI check, boundary test, or gate, and **a skill or a doc line is the
last resort**. That ordering is the workflow. Adding a skill is the weakest
available response, so the workflow's main job is to stop you reaching for one.

Two ratchets landed the same week the question was asked, both preferring code
over prose: `test_substrate_seam_is_vendor_neutral.py` (M2-03's vendor claim) and
the `trackio` / `dvc` / `huggingface_hub` additions to the observability firewall
(M2-09). Neither became a skill.

---

## Part 2 — BRE-9: infrastructure and ELT tiers

> *"Confirm Tier-1 (Postgres+pgvector operational store; OpenTofu or Pulumi
> self-provisioning) and set triggers for Tier-2 (dlt, dbt-core, DuckDB) and
> Tier-3 (Dagster, Cube semantic layer, ClickHouse). Don't over-build."*

### Verdict: Tier 1 is overturned. Tiers 2 and 3 keep their triggers.

**No Postgres. No pgvector. No OpenTofu or Pulumi.** Not "later" — the operational
store the tier describes already exists in a form the tiering did not anticipate,
and installing the tier-1 stack beside it would create the ambiguity that has now
decided four tool choices in this project.

**What the operational store actually is.** An append-only JSONL ledger plus a
`JobRegistry` with an fsynced reservation log. `pyproject.toml` records the reason
it stays that way: `dependencies = []`, because *"the verifier core deliberately
has no runtime deps."* A database under the ledger would make the thing that
adjudicates depend on a service that can be down, migrated, or edited — and
"unreadable ledger means spend is unknown, not zero" is a rule that gets much
harder to keep when the ledger is a network round trip.

**pgvector specifically has no consumer.** Nothing in this repo does similarity
search. `literature.py` reads a pinned `corpus.json`. A vector store with no query
is a dependency with no user.

**OpenTofu / Pulumi has nothing to provision.** C-01 put compute on one local
card. Infrastructure-as-code for one desktop GPU is a build step that describes a
machine you are sitting at. The trigger is real infrastructure existing —
rented instances, or an edge fleet with more than one board.

### Triggers, unchanged in substance and now stated as conditions rather than tiers

| Adopt | When |
|---|---|
| **DuckDB** | the ledger is large enough that `rows()` scanning it is the slow part of a verdict. Measure before believing this; it is a few thousand rows |
| **dlt** | a dataset stops being one pinned git commit — multiple sources, or a snapshot too large to hand-provision. Same trigger as M2-09's DVC decline |
| **dbt-core** | there is a transformation *chain* someone else must audit. One `drone_audio.py` feature step is not a chain |
| **Dagster** | Prefect's trigger fires first (M2-03 names Prefect as the fallback), and it fires when the registry outgrows a file. Dagster is behind Prefect, not beside it |
| **Postgres** | concurrent writers. One machine, ≤3 concurrent jobs, and an fsynced append-only log is the correct primitive at that scale |
| **ClickHouse, Cube** | not before a second consumer of the data exists. Currently: nobody |

**Every trigger is a fact about the system, not a date.** That is the whole
content of "don't over-build": a tier plan whose triggers are calendar quarters
gets adopted on schedule regardless of need, and a tier plan with no triggers at
all gets adopted the first time someone is holding the keyboard and it seems
easier.

---

## What this changes today

Nothing installed, no dependency added, no module changed. Both parts are
ratifications, and in both the honest answer was that the built thing had already
diverged from the plan being ratified — which is the reason to write it down
rather than tick the box.

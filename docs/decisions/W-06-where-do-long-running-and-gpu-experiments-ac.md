---
id: W-06
parent: wayfinder:map
labels: [wayfinder:research]
mode: AFK
blocked-by: []
assignee: driver
status: closed
---

# W-06 — Where do long-running and GPU experiments actually execute?

## Question

Establish the execution substrate for workloads that don't fit a CI runner.

Specifically: sandboxing options for long-running training jobs (container, gVisor, VM, managed
sandbox providers); how a default-deny egress posture survives contact with dataset and model-weight
downloads; how workspace state persists across a multi-hour job; and what the cost envelope looks
like per experiment.

This gates the cost model and the security posture, both currently in fog.

<!-- blocked by: nothing -->

## Resolution

**Verdict: two execution profiles, two substrates. Do not run the agent and the experiment in the
same sandbox.**

They differ on every axis that matters:

| | Agent session | Training / experiment run |
|---|---|---|
| Bound by | waiting on the model | compute |
| Duration | minutes | hours |
| Code trust | **untrusted** (unreviewed, injectable) | trusted (reviewed, or agent-authored under gates) |
| Needs GPU | no | yes |
| Billing shape | active-CPU billing wins | wall-clock is honest; active-CPU saves nothing |

**GPU availability is the hard filter.** Modal, Northflank and Beam Cloud offer GPU sandboxes.
E2B, Vercel Sandbox, Cloudflare Sandboxes, Blaxel, Fly.io Sprites and Runloop **do not**.

**Session caps:** Cloudflare 30 min; Vercel 45 min (Hobby) / 24 h (Pro); E2B 24 h; Northflank,
Modal, Daytona, Beam unlimited.

**Isolation:** Firecracker microVM (E2B, Fly.io Sprites, Northflank), Kata (Northflank), gVisor
(Modal), plain Docker (Daytona — weaker). For untrusted code, microVM is worth the overhead; for
internal automation you control, gVisor suffices.

**Indicative GPU rates:** Modal H100 ~$3.95/hr, A100 40GB ~$2.10/hr, billed per second, with GPU,
CPU and RAM charged separately. Northflank claims all-inclusive GPU pricing ~62% cheaper and offers
BYOC. *Caveat: most of the comparative pricing evidence here comes from Northflank's own blog —
treat the head-to-head numbers as vendor-sourced and re-verify before committing.*

**Recommended shape:** agent runs in the gh-aw container (or E2B) with default-deny egress; when it
needs an experiment, it submits a job to a separate GPU substrate and receives back only a result
artifact. The agent never holds GPU credentials — same pattern as Symphony's `linear_graphql` tool
and gh-aw's zero-secrets layer.

**Unresolved and now fog:** how the default-deny egress posture survives dataset and model-weight
downloads. Allowlisting HuggingFace and dataset mirrors reopens a large exfiltration surface.

## AMENDMENT (post Open SWE research)

The two-substrate finding **stands**, but its build/adopt implication may have loosened. Open SWE
ships pluggable sandbox providers including **Modal**, which offers GPU sandboxes. If an Open SWE
sandbox can hold a multi-hour compute-bound job, part of "agent submits, substrate runs" is available
off the shelf rather than built — which would shrink M2-03 considerably.

Unchanged: the provider survey (GPU only on Modal / Northflank / Beam; E2B, Runloop, Vercel,
Cloudflare, Fly.io Sprites have none) and the vendor-sourced-pricing caveat. Open the question, don't
assume the answer — test against M2-01's 20-minute-hook probe.

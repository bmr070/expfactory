---
id: W-07
parent: wayfinder:map
labels: [wayfinder:task]
mode: HITL or AFK
blocked-by: []
assignee: driver
status: closed
---

# W-07 — Provision the issue tracker

## Question

This map currently lives in local markdown because no tracker doc was configured.

Decide between Linear (already connected; the control plane the whole design assumes; Symphony's
only supported tracker kind) and GitHub Issues (co-located with gh-aw and CI, native to the
Copilot coding agent path). Then provision it: create the project, define the state names the
dispatcher will key on, and write the tracker doc so later sessions know how blocking and frontier
queries are expressed.

AFK where possible; otherwise hand over a precise checklist. Records the resulting facts —
project slug, state names, credential location — that later tickets depend on.

<!-- blocked by: nothing -->

## Resolution

**Verdict: Linear as the human-facing board; GitHub Issues as the machine control plane; a one-way
Linear→Issues sync. Provision now.**

Rationale: the two custom-runner and gh-aw paths from W-08 both key off GitHub — gh-aw is native to
Issues, and the empirical runner's PRs and CI live there too. But Linear is the better human surface
and is Symphony's reference tracker. Resolve the tension with direction: humans triage in Linear,
an `agent-ready`+lane-labelled issue syncs one-way into GitHub Issues, and the runners only ever read
GitHub. This keeps the machine plane in one place while preserving the human ergonomics, and avoids
two-way-sync race conditions.

**Provisioning checklist (HITL where credentials are needed):**
- GitHub: create the factory repo; enable Issues; define dispatcher-relevant labels
  (`agent-ready`, `lane:empirical`, `lane:deterministic`, `blocked`, `needs-human`); create the
  three trust-lane CODEOWNERS entries (migrations/auth/billing → mandatory human) even though the
  proving workload is empirical, so the pattern exists from day one.
- Linear: create the project; map states to the dispatcher's expected set
  (Ready → In Progress → In Review → Done); note that only human-applied `agent-ready` is eligible
  for dispatch (untrusted-tracker-input defense from the Symphony spec).
- Record facts for downstream tickets: repo slug, label set, state names, and that tracker
  credentials live in the runner's secret store — never in an agent workspace.

**Security note carried forward:** the tracker is untrusted input. Dispatch is allowlisted by
human-applied label; no ticket self-promotes.

## AMENDMENT (post Open SWE research)

The one-way Linear→GitHub sync was designed because the candidate runners read GitHub only. **Open SWE
invokes natively from Linear and Slack, and triggers from GitHub labels.** If it is adopted, the sync
layer may be unnecessary machinery — Open SWE reads the human board directly.

What does NOT change: the untrusted-tracker defense. Whatever reads the board, only a human-applied
label is dispatch-eligible. Label-triggered invocation makes this *more* important, not less, since
the trigger surface is now directly on the human-facing tool.

---
id: W-12
parent: wayfinder:map
labels: [wayfinder:grilling]
mode: HITL
blocked-by: [W-08]
assignee:
status: open
---

# W-12 — What is the cost model and where are caps enforced?

## Question

Graduated from fog by W-04 and W-06, which made the numbers concrete.

Two cost surfaces now exist and they behave differently. Agent inference has a native gh-aw guardrail
(`max-daily-ai-credits`, default ~5000 AIC / $50/day, org-overridable, bypassed for manual and
slash-command runs). GPU compute is billed per-second by the substrate at roughly $2-4/hr and has no
such guardrail — a runaway hill-climb spends real money with nothing to stop it.

Resolve: where each cap is enforced, what happens on breach (fail the run, queue, page), whether
budget is per-seat, per-issue or per-experiment, and how cost is attributed back to a ticket. Note
the cautionary precedents already on record: Uber exhausting an annual budget in four months, and
Microsoft cancelling licences over $500-2000/engineer/month.

---
id: W-12
parent: wayfinder:map
labels: [wayfinder:grilling]
mode: HITL
status: RESOLVED
resolved: 2026-07-27
---

# W-12 — What is the cost model and where are caps enforced?

## Verdict

**Two surfaces, two enforcement points, and they are not symmetric.**

| Surface | Cap lives | Status |
|---|---|---|
| GPU compute | `JobRegistry` — per-job and per-day, fail-closed | **Built** |
| Agent inference | The **credential holder**, never the agent | **Specified, not buildable yet** |

Nothing upstream enforces either. Symphony's `SPEC.md` §13.5 specifies token
accounting in careful detail — absolute vs delta payloads, double-counting
avoidance, rate-limit tracking — and the verbs are *accumulate*, *track*,
*report*. There is no budget, no breaker, no refusal anywhere in the spec. That
is measurement presented where enforcement is expected, the same shape as a green
dashboard line, and it means the caps here have no off-the-shelf equivalent.

## GPU compute — done

`JobRegistry` checks before submission, not after:

- `per_job_cap_usd` — a single job whose estimate exceeds it is refused
- `per_day_cap_usd` — a job whose estimate would take trailing-24h spend over it
  is refused
- **fail-closed accounting** — an unreadable or partially-corrupt log refuses
  submission rather than assuming zero spend, because treating a corrupted ledger
  as "no spend so far" hands out an unbounded budget
- the breaker, once tripped, stays tripped

`CostModel` imputes ~$0.09/GPU-hour for owned hardware (electricity plus
amortisation). The precision is fictional and stated to be; the magnitude is what
decides whether a cap binds. Override all three fields on rented compute, at
which point the numbers stop being imputed and become the provider's price.

Attribution is per-job and carries the ticket id, so spend rolls up to a ticket
without a second ledger.

The ticket's framing — *"GPU compute is billed per-second at roughly $2–4/hr and
has no such guardrail"* — was written before C-01 put the work on a local card.
Both halves changed: the rate is two orders of magnitude lower, and the guardrail
now exists.

## Agent inference — the cap cannot live where the ticket assumed

W-12 assumed gh-aw's `max-daily-ai-credits` would cover this. Two things killed
that: gh-aw does not host the empirical lane (W-04), and M2-02 resolved that no
orchestrator is adopted at all. So there is no inherited guardrail.

The obvious build is to have `AgentSession.run` report its token spend and cap on
that. **That is invariant 9 with the numbers changed.**

> You cannot verify a result by asking the thing that produced it what the result
> was.

An agent that reports its own spend can under-report it, for the same reason an
agent that reports its own verdict can set `promoted=True`. A cost cap fed by
self-reported usage is a cap the spender computes — which is not a cap.

**So: agent inference is metered by whatever holds the API credential, never by
the agent.** That is the M2-07 dummy-token proxy pattern doing a second job — the
proxy already sits between the agent and the provider because the agent must not
hold the key, and a component that sees every request is the only honest place to
count them. Same control, no new mechanism.

**Not buildable yet**, and the blocker is named: there is no proxy because there
is no separate agent identity (GH#15). This is recorded as the design rather than
built as a stub, because a stub reading self-reported numbers would look like a
cap while being none.

## The bound that exists in the meantime

`Runner(max_awaiting_human=N)` bounds dispatches per tick, and dispatch is what
spends inference. It is not a dollar cap and must not be described as one — it
bounds *how many sessions start*, not what each costs.

It was built for MAP.md's review-bandwidth constraint, and it turns out to carry
a third job: counting `needs-human` alongside `in-review` means a run of
correlated failures fills the queue and stalls dispatch. That is the circuit
breaker W-08 found missing from Symphony (*"thirty tickets failing on a common
upstream break produce thirty independent retry storms — no circuit breaker"*),
falling out of the review bound rather than needing its own mechanism.

## Cautionary precedents, revisited

The ticket cites Uber exhausting an annual budget in four months and Microsoft
cancelling licences over $500–2000/engineer/month. Both are *agent inference*
failures, not compute failures — which is the surface still uncapped here. They
are the reason the answer above is "specified, not stubbed": the failure mode
those organisations hit is not "no cap existed," it is "spend outran the loop
that was supposed to notice."

Fail-closed accounting on the GPU side is the direct response. The inference side
gets the same discipline when the proxy lands.

## Refs

`src/expfactory/registry.py`, `src/expfactory/local_substrate.py` (`CostModel`),
`src/expfactory/runner.py` (`max_awaiting_human`), GH#15,
`docs/research/agent-factories-2026.md`, `docs/decisions/M2-07-RESOLVED-open-swe.md`.

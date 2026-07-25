        ---
        id: W-11
        parent: wayfinder:map
        labels: [wayfinder:grilling]
        mode: HITL
        blocked-by: [W-08]
        assignee:
        status: open
        ---

        # W-11 — How do review findings become permanent gates?

        ## Question

        Specify the ratchet mechanism: the path from "a review found this" to "this can never
happen again".

Code-review produces findings. improve-architecture produces deepening candidates. Both produce
*reports and conversations* — neither writes a durable constraint. A factory whose improvement
loop terminates in prose does not ratchet, and the whole harness-engineering argument is that
recurring failures must become structurally impossible.

Resolve: where a gate physically lands (lint rule, PreToolUse hook, CI check, architecture test,
harness gate for the empirical lane), who decides a finding is recurring enough to promote,
the cadence, and what stops the gate set from becoming unmaintainable. Blocked on the outer-loop
choice because gh-aw and a Symphony port expose different enforcement points.

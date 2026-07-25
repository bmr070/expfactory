        ---
        id: W-10
        parent: wayfinder:map
        labels: [wayfinder:grilling]
        mode: HITL
        blocked-by: [W-02]
        assignee:
        status: open
        ---

        # W-10 — What is the per-unit-of-work loop?

        ## Question

        Specify the stage chain a single unit of work travels through inside the factory.

Draft proposed in-thread: wayfinder -> to-tickets -> implement/TDD -> code-review ->
improve-architecture. That chain is coherent for the deterministic lane but assumes code with
tests at every stage; the empirical lane needs a different stage 3 (run experiment -> adjudicate
against gates -> record to ledger), which is why this is blocked on the lane decision.

Resolve: the stage list per lane, which stages are agent-driven vs human-in-the-loop, which run
as fresh-context sub-agents, and what artifact each stage hands the next. Note that code-review's
Spec axis consumes the acceptance criteria to-tickets wrote — so slice quality upstream caps
review quality downstream.

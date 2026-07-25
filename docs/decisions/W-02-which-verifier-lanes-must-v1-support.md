        ---
        id: W-02
        parent: wayfinder:map
        labels: [wayfinder:grilling]
        mode: HITL
        blocked-by: [W-01]
        assignee:
        status: open
        ---

        # W-02 — Which verifier lanes must v1 support?

        ## Question

        Does v1 of the factory support the deterministic lane only, the empirical lane only,
or both?

"Designed for various use cases" does not require building both verifiers now. It requires the
substrate — dispatcher, workspace, safe outputs, ledger — to be lane-agnostic, with the verifier
pluggable behind a common interface.

Resolve: is the verifier a plugin boundary in v1, or is v1 single-lane with the boundary deferred?

        <!-- blocked by: W-01 -->

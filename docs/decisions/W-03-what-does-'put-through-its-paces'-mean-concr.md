        ---
        id: W-03
        parent: wayfinder:map
        labels: [wayfinder:grilling]
        mode: HITL
        blocked-by: [W-01]
        assignee:
        status: open
        ---

        # W-03 — What does 'put through its paces' mean concretely?

        ## Question

        Define the acceptance criteria that close this effort's successor build.

Without this the factory is never done and never falsified. Candidate shapes: N tickets driven to
merge without human code edits; M consecutive runs with zero gate bypasses; one deliberately
adversarial ticket from the stress-test suite caught rather than merged; cost per merged unit
under some ceiling.

Must include at least one *negative* criterion — something the factory must refuse to do.

        <!-- blocked by: W-01 -->

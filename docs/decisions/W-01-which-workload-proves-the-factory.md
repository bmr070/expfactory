        ---
        id: W-01
        parent: wayfinder:map
        labels: [wayfinder:grilling]
        mode: HITL
        blocked-by: []
        assignee:
        status: open
        ---

        # W-01 — Which workload proves the factory?

        ## Question

        Pick the single workload driven end to end as the factory's acceptance test.

Candidates raised so far: multimodal drone detection (empirical lane), drone swarm sim2real
(empirical + hardware), a web app (deterministic), an iOS app (deterministic + device/simulator
verification), or something else entirely.

The choice determines which verifier lane gets exercised, and therefore most of the map. Decide
on stress value, not on interest: which workload most reliably breaks a naive factory.

Consider explicitly: iOS is the hardest *deterministic* case because simulator/device verification
is slow and flaky, which stresses a different axis (feedback latency) than the empirical lane
stresses (result validity). That may make it a better or worse proving ground than detection.

        <!-- blocked by: nothing -->

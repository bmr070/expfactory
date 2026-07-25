# 10 — The weekly ratchet: interventions become permanent gates

**What to build:** The factory improves instead of plateauing: every human intervention is tagged with a reason code, and the recurring ones are promoted into deterministic gates at the cheapest sufficient enforcement point, feeding back into the run stage.

**Blocked by:** 08b, 09

**Status:** ready-for-agent

- [ ] Each human intervention is recorded with a reason code
- [ ] A reason code recurring across >=2 runs is promotable; one-offs are logged, not gated
- [ ] A promoted finding lands as a lint rule, PreToolUse hook, CI check, boundary test, or new gate+fixture — prose in AGENTS.md only as last resort
- [ ] New gates feed back into the run stage, never into the wayfinder stage

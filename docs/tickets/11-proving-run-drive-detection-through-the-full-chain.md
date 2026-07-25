# 11 — Proving run: drive detection through the full chain

**What to build:** The whole factory is exercised end-to-end on drone detection and judged against the acceptance suite — the moment the design is proven or falsified.

**Blocked by:** 10, 04

**Status:** ready-for-agent

- [ ] A batch of detection experiments runs unattended through all six stages
- [ ] The gate harness meets the acceptance suite from ticket 04, including held-out fixtures
- [ ] Human-intervention fraction does not climb as batch size grows
- [ ] Cost per adjudicated experiment stays under the configured ceiling
- [ ] A run rejecting every fake improvement is accepted as a passing proof

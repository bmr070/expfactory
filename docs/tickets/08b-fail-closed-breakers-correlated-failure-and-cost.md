# 08b — Fail-closed breakers: correlated failure and cost

**What to build:** One shared actuator — halt dispatch and page the harness owner — fires on either a bad upstream day or a spend overrun, so neither burns money or fills the board with junk silently.

**Blocked by:** 08

**Status:** ready-for-agent

- [ ] A global circuit breaker halts dispatch after N correlated failures and pages the harness owner
- [ ] Per-experiment and per-day cost caps fail closed: breach halts dispatch and moves the ticket to needs-human
- [ ] Neither breaker auto-retries; both require human clearance to resume
- [ ] Failure and cost breakers share the same halt-dispatch mechanism

# 08 — Runner survives restart from durable state

**What to build:** A six-hour training run and an orchestrator restart don't corrupt state: the runner resumes in-flight runs from durable storage rather than orphaning them.

**Blocked by:** 07, 06

**Status:** ready-for-agent

- [ ] Runner state (claimed tickets, in-flight runs, retry timers) persists to durable storage
- [ ] An orchestrator restart resumes in-flight runs rather than orphaning them
- [ ] A compute-tuned stall timeout does not kill a job that is merely compute-bound and quiet
- [ ] Recovery reconciles against the tracker as source of truth on startup

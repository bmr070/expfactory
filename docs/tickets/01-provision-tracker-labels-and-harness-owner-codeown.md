# 01 — Provision tracker, labels, and harness-owner CODEOWNERS

**What to build:** The factory has a control plane: a Linear board synced one-way into GitHub Issues, dispatch labels, and CODEOWNERS trust lanes — so a human-tagged ticket is the only thing a runner will ever pick up.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] Linear project exists with states Ready→In Progress→In Review→Done
- [ ] GitHub repo has Issues enabled and labels agent-ready, lane:empirical, lane:deterministic, blocked, needs-human
- [ ] One-way Linear→Issues sync runs; runners read Issues only
- [ ] CODEOWNERS routes migrations/auth/billing paths to a mandatory human reviewer
- [ ] A ticket without a human-applied agent-ready label is never dispatch-eligible

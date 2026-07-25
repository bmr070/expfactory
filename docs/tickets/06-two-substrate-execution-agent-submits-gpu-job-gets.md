# 06 — Two-substrate execution: agent submits GPU job, gets an artifact

**What to build:** The agent session and the GPU experiment run in separate sandboxes. The agent submits a job to the GPU substrate and receives back only a result artifact — it never holds GPU credentials or shares a box with untrusted training code.

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] A pinned mirror allowlist plus checksums permits dataset/weight downloads without opening general egress
- [ ] Agent runs in a container with default-deny egress; experiment runs on a separate GPU substrate
- [ ] Agent dispatches a job and receives an artifact bundle, never a credential
- [ ] Losing the agent host does not lose committed ledger rows

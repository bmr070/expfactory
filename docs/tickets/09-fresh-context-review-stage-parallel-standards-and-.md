# 09 — Fresh-context review stage: parallel Standards and Spec

**What to build:** Every completed run is reviewed by a subagent with a fresh context window and read-only tools, running Standards and Spec checks in parallel — so the reviewer can't rubber-stamp the implementer's own reasoning.

**Blocked by:** 07

**Status:** ready-for-agent

- [ ] Review runs in an isolated context distinct from the implementer's
- [ ] Standards and Spec run as parallel checks and report separately
- [ ] Reviewer tools are read-only
- [ ] Yellow/red diffs (per CODEOWNERS) require a human gate before merge

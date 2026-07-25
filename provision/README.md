# Ticket 01 — provisioning (needs your accounts; apply by hand)

These are the artifacts the runner and trust model depend on. Creating accounts,
repos, and changing settings are actions only you can take — apply these, don't
hand them to an agent.

## GitHub (the machine control plane)
1. Create the factory repo; enable Issues.
2. `gh label create` each entry in `labels.json` (or import via the API).
3. Commit `CODEOWNERS` to `.github/CODEOWNERS`. Replace `@harness-owner` with the
   real GitHub handle of the one named senior engineer who owns the harness.
4. Branch protection on `main`: require CODEOWNERS review, require CI green.

## Linear (the human board)
5. Create the project; map states Ready -> In Progress -> In Review -> Done.
6. One-way Linear -> Issues sync. Runners read GitHub Issues ONLY.
7. Only a human may apply `agent-ready`. This is the untrusted-tracker defense:
   anyone who can file a ticket can prompt-inject the factory, so dispatch is
   allowlisted by a human-applied label — no ticket self-promotes.

## Secrets
8. Tracker + model credentials live in the runner's secret store. NEVER in an
   agent workspace. The agent requests actions; a gated step performs them.

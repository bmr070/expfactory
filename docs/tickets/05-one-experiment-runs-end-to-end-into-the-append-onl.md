# 05 — One experiment runs end-to-end into the append-only ledger

**What to build:** A single drone-detection candidate goes from config to adjudicated result: trained on the empirical substrate, verified through the plugin, its verdict and full provenance written to the append-only ledger — reconstructable from the ledger alone.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] A candidate experiment trains, is verified, and appends one immutable ledger row
- [ ] The row carries config, code hash, seeds, per-gate verdicts, and cost
- [ ] The experiment reconstructs from the ledger with no reference to agent narrative
- [ ] Ledger history cannot be edited, only appended

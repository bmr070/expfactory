# 02 — Extract the verifier plugin interface from the prototype

**What to build:** expfactory exposes a single verifier boundary — run(candidate)->(verdict, artifact_bundle) — with the existing gates behind it, so the dispatcher never knows which lane it's verifying. Prefactor: this reshapes the prototype before any runner consumes it.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] A Verifier protocol exists returning a verdict plus an artifact bundle
- [ ] The six existing gates run behind that interface unchanged in behaviour
- [ ] A trivial CI-shelling deterministic Verifier implements the same interface (exit code -> verdict), proving the seam admits two implementations
- [ ] promoted remains derived from gates, never settable by a caller

---
id: M2-02
parent: wayfinder:map2
labels: [wayfinder:grilling]
mode: HITL
blocked-by: [M2-01]
assignee:
status: open
---

# M2-02 — Orchestrator: final pick, and is it even load-bearing?

## Question

Reopens Map I's W-08, which was answered twice with different conclusions (reimplement the
loop; then fork Baton) and whose second answer was withdrawn — the Python-seam argument did not
survive scrutiny, since a subprocess boundary costs almost nothing.

**Baton is ELIMINATED as a foundation** (recorded here because it leaked back into an earlier draft
of this ticket after being withdrawn). The Python-seam argument that justified forking it did not
survive scrutiny — a subprocess boundary costs almost nothing, so "same language" was never a real
tiebreaker. Baton is retained ONLY as a ~200-line reading exercise for understanding the loop shape.

Live candidates: OpenSymphony as-is (641 commits, 47 releases, most mature), Kata Symphony (1,311
commits, SSH worker pools), Open SWE (may subsume this layer entirely — see M2-07), or none — if the
split-loop design holds, the orchestrator only handles short coding sessions and the choice is
low-stakes and reversible.

Note the reframing: the *less* the orchestrator does, the less its identity matters. Resolve that
question first, then pick.

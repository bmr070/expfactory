# Domain docs

**Single-context.** One package, one context. No `CONTEXT-MAP.md`.

## Where the docs are

| Kind | Location | Note |
|---|---|---|
| Specification | [`docs/SPEC.md`](../SPEC.md) | The authoritative document. Read before changing anything under `src/`. |
| Decision records | [`docs/decisions/`](../decisions/) | **This repo's ADRs.** Not `docs/adr/`. |
| Maps | [`docs/MAP.md`](../MAP.md), [`docs/MAP2.md`](../MAP2.md) | Closed decisions, open territory, and an explicit rejections list. |
| Agent contract | [`AGENTS.md`](../../AGENTS.md) | Invariants and gotchas. |
| Build slices | [`docs/tickets/`](../tickets/) | |

`docs/adr/` is deliberately **not** created. This repo already had 22 decision
records under `docs/decisions/` before this setup ran, following a
`W-NN` / `M2-NN` naming convention with `-RESOLVED-` marking closure. Adding an
empty parallel directory would split the record and leave a reader unsure which
one is authoritative — the same ambiguity the project spends its effort removing
elsewhere.

There is no `CONTEXT.md`. `AGENTS.md` and `docs/SPEC.md` between them already
serve that role, and a third overview would be a third thing to keep true.

## Consumer rules

**Read before writing.** Any change under `src/expfactory/` touches the
verification layer. `docs/SPEC.md` and the numbered invariants in `AGENTS.md` are
prerequisites, not references.

**Respect the rejections list.** [`docs/MAP.md`](../MAP.md) has an *"Explicitly
rejected"* section of options declined on evidence, several of them proposed
repeatedly. Re-proposing one wastes a session. Check it before suggesting an
architecture change.

**A decision record beats a docstring.** Where code and a `-RESOLVED-` decision
disagree, the decision is the intent and the code is the bug — unless the code
carries a comment explaining a deliberate deviation.

**Amendments, not edits.** A decision that turns out wrong gets an amendment
record that supersedes it. The original stays, so the reasoning trail survives
and a future reader can see what changed and why.

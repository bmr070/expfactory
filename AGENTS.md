# AGENTS.md — expfactory

Entry point for any coding agent, per the [agents.md](https://agents.md) standard.

**The full instructions live in [`CLAUDE.md`](CLAUDE.md). Read that.** This file
is deliberately thin: the two would drift if both carried the same content, and a
stale copy of a safety rule is worse than no copy.

What follows is only what an agent must know before touching anything.

## Setup and test

```bash
pip install -e ".[dev]"
pytest
ruff check src tests && ruff format --check src tests
mypy
python -m expfactory.selfcheck
```

## Four rules, before your first edit

1. **Never push to `main`.** Branch, then open a PR. A pre-commit hook blocks
   edits on `main` and branch protection blocks the push.

2. **`src/expfactory/` is verification substrate.** A PR touching it fails
   `substrate-guard` on purpose, and that failure is not a bug to route around —
   it requires a deliberate human override, recorded in the PR timeline. Adding a
   module there also fails the test suite until the module is classified in
   `_HARNESS_PATHS` or `_NOT_SUBSTRATE` in `gates_v1.py`, plus a line in
   `.github/CODEOWNERS`.

3. **A run where every proposed improvement is correctly rejected is a passing
   run.** This factory verifies empirical claims. If you find yourself trying to
   make something get promoted, stop.

4. **Tests and docs ship in the same commit as the change.** Not the next PR.

## Where to look

| For | Read |
|---|---|
| Everything: architecture, commands, standards, invariants, gotchas | [`CLAUDE.md`](CLAUDE.md) |
| The specification | [`docs/SPEC.md`](docs/SPEC.md) |
| Who is trusted and who adjudicates | [`docs/ROLES.md`](docs/ROLES.md) |
| A word that seems to mean two things | [`CONTEXT.md`](CONTEXT.md) |
| Why a decision went the way it did | [`docs/decisions/`](docs/decisions/) |
| What is left to build | [`docs/tickets/NEXT.md`](docs/tickets/NEXT.md) |
| Before dispatching a real agent | [`docs/DISPATCH-READINESS.md`](docs/DISPATCH-READINESS.md) |

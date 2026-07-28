---
name: ratchet
description: Turn a recurring mistake into the cheapest check that makes it structurally unreachable, instead of writing a note asking people to remember. Use after a bug recurs, a review finding repeats, or a rule keeps being forgotten. Trigger words - this keeps happening, make sure we never, prevent this, add a rule, remember to, second time.
---

# Ratchet a failure

> **Prose does not ratchet.** (invariant 8)

A line in a doc asking someone to remember is the weakest possible control and
the one most often reached for. The rule here is the opposite: find the cheapest
point that makes the failure *structurally unreachable*, and put the check there.

## The ladder, cheapest first

```
lint rule  <  pre-commit hook  <  CI check  <  boundary test  <  gate  <  prose
```

Go as far left as the failure allows. Prose is the last resort, not the first.

| The failure is | Put the check at |
|---|---|
| A code pattern | lint rule |
| Something about the working copy | pre-commit hook |
| Something about the diff or the branch | CI check |
| The assembled system reaching a wrong verdict | boundary test |
| A candidate that should never promote | gate + fixture |
| Genuinely a judgement call | prose, and say why nothing cheaper works |

## Steps

1. **Name the failure as a condition, not a feeling.** "Two tickets can map to
   one workspace directory", not "be careful with names".

2. **Find the cheapest rung that catches it.** Ask what would have to be true for
   the failure to be impossible rather than merely discouraged.

3. **Write the check so it fires.** Then *prove* it fires: restore the bug, run
   the check, watch it object. A check that cannot catch its own motivating bug
   is theatre, and there is no way to know without putting the bug back.
   `test_the_probe_catches_the_bug_it_was_written_for` is the pattern.

4. **Guard the guard.** A check over a list, a glob, or a set of files needs a
   test that the list is not empty and still matches reality. A glob matching
   nothing makes every assertion below it vacuously true.

5. **Record what it cost.** One line in `docs/GOTCHAS.md` with the *why*, so the
   next person does not undo it as noise.

## Worked examples in this repo

- **New module goes unprotected** → a test derives the module list from the
  filesystem and fails until each is classified. Caught `runner.py`,
  `github_tracker.py`, `llm_probe.py` and `sandbox.py` automatically.
- **A doc link rots** → a test resolves every relative markdown link.
- **A gate silently stops passing anything** → property sweep, not more fixtures.
- **Two docs drift into stale copies of one rule** → a test bounds the size of the
  pointer file and asserts the numbered list appears in exactly one place.

## Traps

- **Do not grep source text to check code.** Two tests here matched their own
  docstrings. Parse the AST and inspect imports or calls.
- **Over-strict beats under-strict for a firewall**, but a check that cries wolf
  gets skimmed. Six false alarms is worse than zero.
- **A check nobody runs is prose with extra steps.** If it needs a model server
  or an account, say so, and make "did not run" a distinct outcome from "found
  nothing".
- **Skillify success too.** The second time you do a flow by hand, codify it. The
  third time should be a command.

## Related

`docs/decisions/W-11-*.md`, `docs/SPEC.md`, `docs/GOTCHAS.md`.

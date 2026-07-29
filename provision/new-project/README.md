# Bringing a new project into the factory

**This is not `provision/README.md`.** That one stands up the *factory itself*
and is a one-time thing that already happened. This one onboards a *project* the
factory will work on, and runs once per project.

**Apply by hand. Do not delegate this to an agent.** Creating repos, installing
Apps and changing protection settings are account actions.

---

## The rule this exists to enforce

**A project enters as a ticket, and no implementation ticket may exist until the
chain above it exists and is linked.**

```
wayfinder map  →  spec  →  tickets  →  implement  →  code-review
 (Linear project) (issue)  (issues)
```

This is **preregistration applied to engineering work**, and it is G-07's
mechanism in a different lane. G-07 refuses a run whose preregistration is not at
a strictly earlier ledger position, because a metric chosen after seeing the data
is a metric that already moved. The intake gate refuses a ticket whose spec does
not precede it, because a justification written after the code always fits the
code.

In both cases the *ordering* is the control, and in both cases it is checkable
rather than a matter of discipline.

---

## 1. The repository — a project gets its own

Not tidiness. **Invariant 9 at the repo boundary.**

If project code lived beside the verifier, an agent with write access would be
one file from `gates_v1.py`. `substrate-guard` catches that, but only as a
PR-level check after the fact, and it is admin-mergeable. The party being judged
must not be able to reach the judge.

```
bmr070/expfactory     the verifier. Harness-guarded. No project code, ever.
bmr070/<project>      the project. Ordinary software, ordinary CI.
```

**The project repo never imports expfactory.** It emits predictions and
artifacts; runner-owned code holds the labels and computes the metric. That is
the trusted-scorer split (T-01, BRE-27) applied one level up. A project that
depends on its own judge has lost the property the whole design is for.

## 2. Linear — one queue, no mirror

One workspace. **One Linear project per factory project.** Do not create a second
workspace and do not put tickets in the project repo.

Putting tickets in each repo and syncing them up is the Linear → GitHub mirror,
and the W-07 amendment already removed it: *remove the mirror and the two-way
state race cannot occur*. Reintroducing it once per project multiplies the race
that was deliberately deleted.

Pointers are one-directional. The ticket names its repo; the PR names the ticket
id. Nothing syncs state.

Workflow states: `Ready → In Progress → In Review → Done`, plus
**`Running Unattended`** for the detached-job path (W-06, M2-03).

## 3. Labels

Apply every entry in [`../labels.json`](../labels.json). Three groups, and the
`stage:*` group is what makes the gate checkable:

| Group | Labels | Purpose |
|---|---|---|
| stage | `stage:wayfinder`, `stage:spec`, `stage:ticket`, `stage:review` | position in the pipeline |
| lane | `lane:empirical`, `lane:deterministic` | **which verifier owns the outcome** |
| state | `agent-ready`, `blocked`, `needs-human`, `declined` | dispatch and status |

**`agent-ready` on anything that is not `stage:ticket` is a configuration
error.** You cannot dispatch an agent at a question or at a specification. The
`agent-ready-guard` workflow enforces this alongside the human-allowlist check.

**A missing `lane:` is refused, never defaulted.** The lane decides whether phase
two applies at all, and defaulting it is precisely how the two lanes get
conflated — the failure `factory-chart.html` calls the one that sinks this.

## 4. The agent identity

Install the same GitHub App as the factory, with the same permissions and the
same two withheld:

```
contents:        write
pull_requests:   write
issues:          read
metadata:        read

administration:  NONE    # it could otherwise disable its own branch protection
issues:          NOT write
```

Add it as a **write collaborator** so its PRs are reviewable. Do **not** put it
in `CODEOWNERS`. It must be able to open the red lane and never to approve it.

## 5. Branch protection — declared in the repo, not only in the UI

Copy [`../branch-protection.json`](../branch-protection.json), adjust
`repository`, and apply it.

**Keep the file.** Policy that lives only in GitHub settings is invisible to
reviewers and shows up in no diff. That is not a hypothetical: this factory's own
`DISPATCH-READINESS.md` §2 described its protection from memory and omitted
`substrate-guard` from the required checks, and nothing caught it.
`tests/test_branch_protection_policy.py` is the ratchet that now makes the prose
and the policy unable to disagree. Give a new project the same ratchet.

**Do not add `substrate-guard` to a project repo.** It guards the verification
harness, and a project has none. Copying it there is cargo-culting a control that
protects nothing while looking like protection, which is the failure mode
`CODEOWNERS` already warns about.

## 6. Codebase conventions

| File | Holds |
|---|---|
| `CLAUDE.md` | what this project is, **its lane**, its commands |
| `AGENTS.md` | thin pointer at `CLAUDE.md`, same as the factory |
| `CONTEXT.md` | the domain glossary, so `/tdd` and `/domain-modeling` share vocabulary |
| `.github/CODEOWNERS` | see [`CODEOWNERS.template`](CODEOWNERS.template) |

Gate lane in CI from day one: test, lint, format check, typecheck. Both `src`
and `tests` — a stray long line in a test has failed a build in the factory repo.

## 7. Empirical projects only — before anything is dispatch-eligible

A `lane:empirical` project needs these *before* a hill-climb can be preregistered,
because G-07 rule 8 compares a declared baseline against what the ledger records:

- **The dataset pinned by commit SHA**, hand-provisioned. Never fetched at run
  time — this is why `github.com` is not on the egress allowlist.
- **A group-aware split policy**, decided and written down. For sensor data the
  group is usually the source recording; splitting naively teaches the model the
  recording rather than the phenomenon, and the number looks excellent.
- **A baseline that has been RUN and appended to the ledger.** A baseline you
  type is worth what a baseline you invent is worth.
- **The scorer living runner-side**, holding the labels the job never sees.

---

## The shortest path

1. Create the repo, apply labels, apply branch protection from the JSON
2. Install the App; add as write collaborator; keep it out of `CODEOWNERS`
3. Create the Linear project and its states
4. File **one** ticket holding the ambition, and nothing else
5. Run `/wayfinder` on it. Resolve the frontier one node at a time
6. `/to-spec` → `/to-tickets` → `/implement` → `/code-review`
7. Only then does a human apply `agent-ready`, **to one `stage:ticket`**, by hand

Step 4 is the one people skip. The ticket is allowed to be vague — that is what
step 5 is for. What it is not allowed to do is jump to step 7.

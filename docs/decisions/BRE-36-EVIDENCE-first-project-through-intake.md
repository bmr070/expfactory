---
id: BRE-36-EVIDENCE
parent: docs/TRACKING.md
labels: [wayfinder:finding]
status: RECORDED — the chain was walked once; three defects in it
walked: 2026-08-03 to 2026-08-04
project: edge-drone
---

# The intake chain, walked once, and what it cost

BRE-36 specified how a project enters the factory. `edge-drone` is the first one
to actually do it, and this records what the exercise found — because a process
that has been described and never run is a process nobody has tested.

Repo: `bmr070/edge-drone` (private). Linear project: *edge-drone — deployment-
shaped acoustic detection*. CI green on 3.11 and 3.13.

## The chain, as walked

| Stage | Artifact | Held up? |
|---|---|---|
| wayfinder | `docs/WAYFINDER-longform-synthesis.md` | yes |
| spec | `docs/DECISION-deployment-shaped-evaluation.md` | yes |
| tickets | BRE-49, BRE-50, labelled on all four dimensions | yes |
| implement | 21 commits, 186 tests, CI green | yes |
| code-review | review + independent validation, every P0/P1 closed | yes |

The **ordering** earned its keep twice, and both times before any expensive work:

- The wayfinder stage asked "does DADS overlap our dataset" and the answer —
  from one HTTP fetch of a dataset card — was **yes, it contains ours**. Had that
  question come after the corpus was built, the corpus would have been built on a
  leak that reads as external validation.
- The spec stage asked what the metric should be, and found that
  `Pd@1%FAR` over 1-second clips is **36 false alarms per hour**. Every number
  the project had published was at an operating point nobody would deploy.

Neither is a coding finding. Both came from the pipeline order forcing a question
early, which is the whole claim BRE-36 makes.

## Three defects in the chain itself

### 1. Branch protection cannot be provisioned on a free private repo

BRE-36 assumes every project repo gets what `expfactory` has — protected trunk,
required checks, no force-push. GitHub:

    403 Upgrade to GitHub Pro or make this repository public to enable this
        feature.

So **the intake chain is not uniform across projects**, and `provision/` cannot
deliver what it implies. Three resolutions, all the owner's: make project repos
public, accept weaker guarantees on them, or pay.

Until then `CODEOWNERS` in a project repo documents intent rather than enforcing
anything, and it says so in its own header — a file whose presence implies a
guarantee it does not provide is the failure this project exists to catch,
wearing repository furniture.

### 2. Nothing linked the factory to its project

`expfactory` contained **no reference to `edge-drone` anywhere** until this file.
The chain was walked and left no trace on the side that defines it, so from the
factory's own documentation the intake design remained untested.

`provision/new-project/` should emit this link as a step, not leave it to
whoever remembers.

### 3. The default branch is a stage name

`edge-drone`'s default branch is `stage/wayfinder`. That is a *stage* from the
chain, not a trunk, and it became the default because it was the first branch
pushed. A project that finishes the chain has no obvious branch to merge into.

`provision/new-project/` should create `main` and treat `stage/*` as working
branches off it.

## What this says about the design

The chain works. Its ordering caught two findings that would each have invalidated
downstream work, and it caught them for the price of a web fetch and a
twenty-line script.

What does not work is the **provisioning**: it assumes a plan tier it does not
check, produces no link back to the factory, and leaves the branch layout to
chance. Those are three concrete edits to `provision/new-project/` rather than a
rethink, and they are only visible because the chain was walked rather than
described.

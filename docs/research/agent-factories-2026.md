# Agent software factories, surveyed against our open tickets

Read on 2026-07-27. The question is narrow: **do the remaining open tickets survive
contact with what these systems actually do?** Not "what are they" — that was
surveyed in W-05/W-08/M2-07 — but "does anyone upstream already answer the
questions we still have open, and do their answers change ours."

> **Addendum 2026-07-28 at the end of this file** answers the two questions this
> pass left open, and **corrects two conclusions** — GH#15 (Open SWE solves agent
> identity, and the safe mode is its default) and BRE-21. Read it before acting on
> anything here about identity.

Sources read as code and normative text, not as blog posts:

| System | What was read | Status |
|---|---|---|
| [openai/symphony](https://github.com/openai/symphony) | `SPEC.md`, 2,311 lines, in full for §8, §11, §13–15, App. A | Spec + Elixir reference |
| [kumanday/OpenSymphony](https://github.com/kumanday/OpenSymphony) | repo tree, crate layout, README | Rust, 74★, 16 crates |
| [Factory-AI/factory](https://github.com/Factory-AI/factory) | `docs/cli/droid-exec/overview.mdx`, `docs/cli/byok/ollama.mdx` | **Docs only — no droid source** |

**Correction to the brief:** there is no public factory.ai droid codebase.
`Factory-AI/factory` is a documentation site — `README.md`, `docs/`, and nothing
else. Everything below about droid is from its *documented* CLI contract, which
is precise enough to be useful (the `--help` output is reproduced verbatim in
their docs) but is not code I have read. Stated here rather than left for a
reader to discover, because "we validated against Factory's codebase" would be a
claim the evidence does not support.

---

## The through-line

Every control in these systems keys on one of two things, and which one decides
whether we can reuse it.

**Keyed on the actor — cannot be reused here:**

- **Symphony §11.5** — "The service remains a scheduler/runner and tracker
  reader." Ticket mutations, including state transitions, are written *by the
  coding agent* through provider-native tools. The orchestrator does not
  adjudicate; it dispatches and reads.
- **Factory mission mode** — `--validator-model <id>` selects the model that
  validates the workers' output. The validator is a *model choice*, in the same
  process, with the same tools.

**Keyed on the operation — reusable, and we already do the equivalent:**

- **Factory `--auto low|medium|high`** — the tier is a property of the command.
  `git commit` is medium; `git push` is high; `curl | bash` is high. Read-only is
  the default and mutation is opt-in.
- **Symphony §15.2** — workspace path must remain under the workspace root. A
  path check, not an identity check.

This is invariant 9 for the fifth and sixth time, and the first time it has shown
up in someone else's system rather than in ours. Symphony's boundary is *correct
for Symphony*: when CI is the arbiter, letting the agent move the ticket is safe,
because the agent cannot fake a green test suite. It is wrong for us for exactly
the reason T-01 exists — when the deliverable **is** the number, the actor that
produces it cannot also be the actor that records it.

Worth saying plainly: this is not a criticism of Symphony. It is the same design
under a different threat model, and noticing that is most of the value of having
read it.

---

## Ticket-by-ticket

### GH#15 / BRE-18 — agent identity · **SUPERSEDED — read the addendum**

> **This section's conclusion is wrong and kept for the record.** It was reached
> by reading specs and docs. Searching the *code* found that Open SWE solves this
> and that the safe mode is its default. See the 2026-07-28 addendum at the end
> of this file. The material below about §15.1 and §15.3 still stands; the
> heading's "no upstream answer" does not.

Symphony §15.1 is the whole finding:

> Each implementation defines its own trust boundary.
> Implementations SHOULD state clearly whether they are intended for trusted
> environments, more restrictive environments, or both.

The spec **declines to define one** and requires only that you say which you
chose. So there is no upstream position to adopt, and #15 cannot be resolved by
citing a reference implementation. It is genuinely ours.

Factory offers no separation either: droid runs under the operator's own
credentials, and `docs/cli/account` is about a Factory API key, not a distinct
commit identity.

**§15.3 is a check we already pass**, and worth recording as such rather than as
a gap:

> Do not pass tracker credentials through the coding-agent child environment.
> Adapters MUST declare secret environment names so local and remote launchers
> can remove them from child environments.

Our `linear_tracker.py` and `github_tracker.py` both take an injected transport
and never see a token; `registry.py` holds the GPU credential and the agent never
does. That is the M2-07 dummy-token pattern, and Symphony makes it normative. One
gap against the MUST: we do not *declare* secret env names for scrubbing from a
child environment, because we do not yet launch one. That becomes live the moment
a harness does.

### W-12 — cost model and caps · **CONFIRMED UNMET UPSTREAM, one mechanism to adopt**

Symphony §13.5 accounts for tokens in detail — absolute vs delta payloads,
double-counting avoidance, rate-limit tracking — and **caps nothing**. The verbs
are "accumulate", "track", "report". Rate limits are handled by "track the latest
rate-limit payload seen in any agent update." There is no budget, no breaker, no
refusal anywhere in the spec.

That is the same failure mode as a green dashboard line: measurement presented
where enforcement is expected. Our `CostModel` and caps are not redundant with
anything upstream.

**Adopt: §8.3 per-state concurrency limits.**

> `max_concurrent_agents_by_state[state]` if present, otherwise fallback to the
> global limit.

MAP.md's day-one constraint was "any design that raises agent concurrency without
raising review capacity is rejected by default," and it has been prose since. A
per-state cap on `Human Review` is that constraint as config: the queue in front
of a human is bounded, and dispatch stalls rather than piling up. It is the
cheapest rung on the W-11 ladder that can hold this, and it is already specified.

### M2-03 / BRE-16 — experiment queue · **RATIFY**

Symphony §14.3, unambiguous and by design:

> Current design is intentionally in-memory for scheduler state. […] It does not
> mean retry timers, running sessions, or live worker state survive process
> restart.

Recovery is re-polling the tracker and reusing preserved workspaces. For a
six-hour training run, an orchestrator restart orphans the job — the W-05 finding,
now confirmed against the current spec rather than a summary of it. A durable
experiment queue is not something we are building because we failed to find the
off-the-shelf one.

### M2-02 — orchestrator: final pick · **RESOLVE AS "NOT LOAD-BEARING"**

New evidence since the last look.

**OpenSymphony has become a platform, not an orchestrator.** It is now 16 crates
including `planning`, `memory`, `code-intel`, `gateway`, `openhands`, and a TUI —
and `crates/opensymphony-gateway/src/lib.rs` is **235 KB in one file**. Whatever
that is, it is not a small readable loop, and adopting it means adopting a
planning system and a memory system we did not ask for. It stays a reading
exercise. The memory-bucket design is still the part worth reading.

**Appendix A now specifies the SSH worker extension.** This was Kata's
distinguishing feature when Kata was a candidate; it is now in the spec proper,
with the scheduling semantics written down — per-host caps, prefer-previous-host
on retry, and:

> When all SSH hosts are at capacity, dispatch SHOULD wait rather than silently
> falling back to a different execution mode.

That is the external-compute path from C-01 ("local GPU now, edge or external
later"), already specified. Kata drops out entirely: the thing worth taking from
it is now upstream text.

**Recommendation:** close M2-02 as *not load-bearing*. Treat `SPEC.md` §8 and §16
as the specification our runner conforms to where the semantics fit, and
Appendix A as the design for GPU dispatch when compute leaves this machine. Adopt
no orchestrator codebase. The reframing in the ticket — "the *less* the
orchestrator does, the less its identity matters" — turned out to be the answer.

### GH#5 — G-08 churn threshold · **UNCHANGED. The adjacent risk is not G-08's.**

Appendix A.3, on remote failover:

> Once a run has already produced side effects, a transparent rerun on another
> host SHOULD be treated as a new attempt, not as invisible failover.

My first reading was that this threatens G-08's count — that a substrate silently
rerunning a job would make an honest author look like a metric-shopper. **That is
wrong, and checking it is what makes the finding worth recording.** `G-08` counts
*preregistrations* in a lineage (`lineage_attempts`, `prereg.py:233`), not job
runs. A preregistration is filed by an author before the data is seen; no
infrastructure retry files one. The count is immune to invisible failover by
construction.

The risk Appendix A.3 describes is real here, but it lands on the **other**
control. A silent rerun means *additional evaluations under a single
preregistration* — more looks at the holdout than the declaration accounts for.
That is a Ladder question (holdout leakage is driven by feedback, not by query
count) and not a churn question, and the two have different owners: G-08 watches
the author, the Ladder watches the budget.

So #5's threshold work is unchanged. What Appendix A.3 adds is a question for the
substrate, not the gate: **does a silently-retried run submit to the scorer
twice?** If it does, the Ladder is being spent without anyone declaring it.
Recorded here; not filed as a ticket until the substrate can actually retry,
which today it cannot.

### GH#3 / M2-01 — timeout and handoff test · **UNCHANGED**

The spec confirms the numbers M2-01 was going to measure — `turn_timeout_ms`
defaults to one hour, `stall_timeout_ms` to five minutes, and §10.6 maps timeouts
to error classes. Still confirmatory, still needs the owner's machine and
accounts. No change.

### GH#46 — egress pinning bootstrap · **UNCHANGED, no upstream help**

Symphony §15.5 recommends "network restrictions" as one of several possible
hardening measures and specifies nothing. Nobody upstream has a trust-on-first-use
ceremony. Options 1 and 2 in the issue still stand on their own.

---

## Ollama and OpenRouter — the hardware answers this

Factory ships BYOK adapters for both ([`byok/ollama.mdx`](https://github.com/Factory-AI/factory/blob/main/docs/cli/byok/ollama.mdx),
`byok/openrouter.mdx`), local Ollama via an OpenAI-compatible endpoint at
`http://localhost:11434/v1` with `"apiKey": "not-needed"`. So the plumbing is a
config file, not a project.

The constraint is the GPU. Factory's own guidance:

> Models below 30 billion parameters have shown significantly lower performance
> on agentic coding tasks. […] generally not recommended for production coding
> work or complex software engineering tasks.

Their table puts 30B at **20 GB VRAM**. This machine is an **RTX 4070 with 12 GB**.
A 30B model does not fit, and a 7B model is one Factory explicitly does not
recommend for the job.

Two consequences, and they point in opposite directions:

1. **Ollama is not viable as an agent-harness worker on this machine.** Not a
   licensing or integration question — it does not fit in the card. Worker-class
   models come from OpenRouter.
2. **Ollama is fine for the adversarial prober**, because probing gates is not
   agentic coding. The task is "emit a candidate that might slip past a blocking
   gate" — narrow, structured generation with a verdict as ground truth, scored
   by trusted code. A 7B model that produces mostly-garbage candidates at zero
   marginal cost is a *good* fuzzer; the gate set is the judge, and it does not
   care how clever the attacker was.

One thing to design around: the prober and the experiment share one 12 GB card.
They cannot both hold it. Whichever way that resolves, it belongs in the local
substrate's admission control, not in the prober.

---

## What was not found

- **No public factory.ai droid source.** Docs only.
- **No cost enforcement in any of them.** Accounting yes, caps no.
- **No verification of empirical claims anywhere.** Every system surveyed treats
  CI as the arbiter. Nothing adjudicates a number. That was the thesis on day one
  and nothing here contradicts it.
- **No identity separation.** Symphony punts to the implementer; Factory runs as
  the operator. GH#15 has no upstream answer to adopt.

## Sources

- [OpenAI — An open-source spec for Codex orchestration: Symphony](https://openai.com/index/open-source-codex-orchestration-symphony/)
- [openai/symphony `SPEC.md`](https://github.com/openai/symphony/blob/main/SPEC.md)
- [kumanday/OpenSymphony](https://github.com/kumanday/OpenSymphony)
- [Factory — Droid Exec (Headless)](https://github.com/Factory-AI/factory/blob/main/docs/cli/droid-exec/overview.mdx)
- [InfoQ — OpenAI Open-Sources Symphony](https://www.infoq.com/news/2026/05/openai-symphony-agents/)

---

# Addendum, 2026-07-28: the two open questions, answered from source

The first pass left two questions the docs could not settle. Both are now
answered by reading the repositories, and one **corrects a conclusion this repo
had already recorded**.

## 1. Does anyone enforce a cost cap, or only report spend?

**Still nobody, at the orchestrator layer. The best available move is
delegation.**

`openai/symphony` has no budget, cap or spend token anywhere in `SPEC.md`
(searched; zero hits). §13.5 accounts and reports.

`kumanday/OpenSymphony` has two things that look like caps and are not:

- **`max_turns`** on the run schema, commented *"Configured turn budget. A value
  of 0 means the budget is unknown."* It appears in `gateway-schema`,
  `gateway/lib.rs` and `openhands/session.rs` — all **plumbing**. It is recorded
  and passed through; nothing was found that refuses a turn on it. A turn count
  is a proxy for cost anyway, and a poor one, since turn cost varies by an order
  of magnitude.
- **`max_budget_usd: 5`**, which appears **exactly once** in the whole repo, in a
  workflow template inside `opensymphony-workflow/src/lib.rs`:

  ```yaml
  openhands:
    conversation:
      confirmation_policy:
        max_budget_usd: 5
  ```

  This is YAML front matter **passed through to OpenHands**. OpenSymphony does
  not parse or enforce it; the agent runtime's own `confirmation_policy` does.

**That delegation is the finding, and it agrees with W-12.** The cap is placed at
the runtime that holds the model credential, because that is the only component
that sees every request. W-12 concluded the same thing from the other direction:
a cap fed by self-reported usage is a cap the spender computes, so it belongs at
the credential holder. Independent arrival at the same design.

**Consequence for W-12:** the inference cap is not blocked on building a metering
proxy from scratch. If the agent runtime has a budget setting, set it there and
record that the enforcement is delegated. Still blocked on GH#15 for *which*
runtime and *whose* credential, but the shape is now confirmed rather than
inferred.

## 2. Does anyone separate the agent identity from the operator's?

**Yes. Open SWE does, it is the default, and BRE-21 recorded the opposite.**

`docs/INSTALLATION.md` §4b, verbatim:

> Without it, all agent operations use the GitHub App's installation token
> (a shared bot identity).
>
> - **With per-user OAuth**: PRs and commits show the triggering user's identity
> - **Without it (bot-token-only mode)**: all PRs and commits appear as the
>   GitHub App bot

And the environment block says it in one line:

```bash
# === Agent-runtime GitHub OAuth via LangSmith (optional) ===
# Without these, all agent operations use the GitHub App's bot token.
# With these, each agent run authenticates as the triggering user.
GITHUB_OAUTH_PROVIDER_ID=""
```

### The correction

**BRE-21** recorded that Open SWE "opens PRs as the triggering human", read from
`agent/webhooks/linear.py`, and treated it as an inherent property that
degrades CODEOWNERS. That behaviour is real but **configuration-dependent, and it
is the opt-in path**. Leave `GITHUB_OAUTH_PROVIDER_ID` unset and every PR and
commit carries the GitHub App bot identity, which is exactly the separation GH#15
needs.

M2-08 concluded that adopting Open SWE was survivable *because* `substrate_guard`
keys on what changed rather than who. That reasoning still holds and is still
worth keeping. But it was load-bearing on a premise that turns out to be a
setting.

### What the reference implementation actually does

Worth copying rather than reinventing:

| Mechanism | Where |
|---|---|
| Short-lived installation tokens, **scoped per repo and per permission** | `get_github_app_installation_token_with_expiry(repositories=[...], permissions=...)` |
| Sandbox never stores a real token; a proxy injects Basic/Bearer auth so commands run `GH_TOKEN=dummy gh ...` | `agent/utils/github_proxy.py`, `_configure_github_proxy` |
| Token refresh on expiry, with a recorded expiry per proxy | `record_proxy_token_expiry` |
| At-rest encryption with **documented key rotation** — an ordered key list, most-recent-first; new writes use the first, reads try all | `TOKEN_ENCRYPTION_KEY` |
| Repo/org allowlist that **fails closed on any API error** | `ALLOWED_GITHUB_ORGS`, `ALLOWED_GITHUB_REPOS` |

The dummy-token proxy was already adopted in principle (M2-07). What is new is
that the scoping, refresh, rotation and fail-closed allowlist are all there too,
in Python, MIT-licensed, and readable.

### Consequence for GH#15

The **design** half is answered: a GitHub App installation token, kept off the
agent by a proxy, is the mechanism, and there is a working reference. What
remains is genuinely an account action — creating the App and installing it —
which is what the ticket always said.

One thing to carry across: Open SWE's org membership check "fails closed on any
API error", and its docs warn that granting `ALLOWED_GITHUB_ORGS` without the
`Organization → Members: Read-only` permission rejects **every** login. That is
the same failure shape as an unreadable ledger meaning unknown spend rather than
zero, and it is the right direction to fail in.

## 3. Unlooked-for: Open SWE has a circuit breaker

`agent/middleware/sandbox_circuit_breaker.py`. W-08 recorded that Symphony has
none and that thirty tickets failing on one upstream break produce thirty
independent retry storms. That gap is Symphony's, not the whole field's. Ours
falls out of the review bound (#51); theirs is explicit middleware and comments
on both GitHub and Linear when it trips.

## Method note

All of the above came from GitHub code search against the repositories, not from
documentation or write-ups. The first pass read `SPEC.md` and the droid docs and
concluded "nobody solves identity" — which was true of what those documents say,
and false of what the code does. Search the repo, not the README.

---

## Addendum, 2026-07-28: landscape expansion and one correction

Additional official-site and public-repository research covered OpenHands,
SWE-agent, Tessl, Factory's public Action, GitHub Agentic Workflows, and current
OpenAI Codex security guidance. The central conclusion stands: these systems
automate software delivery; none is an authority for an empirical metric. Three
reusable controls are now clearer.

### Correction — `gh-aw` does enforce an inference budget

The statement in §1 that no upstream orchestrator enforces a cost cap is now too
broad. [GitHub Agentic Workflows cost management](https://github.github.com/gh-aw/reference/cost-management/)
documents hard defaults of 1,000 AI Credits per run and 5,000 per workflow per
day, configurable with `max-ai-credits` and `max-daily-ai-credits`. This is a
real control at the agent-inference credential boundary, not reporting.

It does **not** change W-12's GPU conclusion: AI Credits do not reserve, meter,
or cap an hours-long compute job on the experiment substrate. The correct split
is now explicit: delegate token caps to the runtime/provider that holds the
model credential; keep GPU caps, reservation and breaker in `JobRegistry`.

### Adoptable pattern — safe outputs as a separate credentialed job

`gh-aw` compiles Markdown into a locked workflow and keeps the agent read-only by
default. A later [safe-output](https://github.github.com/gh-aw/reference/safe-outputs/)
job validates the agent's requested effects, sanitizes text, and applies only
allowlisted operations with a separate credential. The agent returns an artifact;
the privileged job decides what it may do.

This is the closest off-the-shelf pattern to invariant 9. Adapt the *shape*, not
the GitHub-Action runtime: an agent should request or identify a compute artifact;
runner-owned code fetches trusted completion metadata, runs the scorer, and only
then constructs evidence for the verifier. Safe output validation cannot itself
verify an ML metric, so the scorer/provenance remain ours.

### Context is an evaluated input, not a control

[Tessl's context lifecycle](https://docs.tessl.io/introduction-to-tessl/context-lifecycle)
and [evaluation documentation](https://docs.tessl.io/evaluate/evaluating-your-codebase)
provide a useful complement to the ratchet. Version skills, documentation and
rules; run representative scenarios with and without them; measure whether they
improve agent behavior before rollout. That belongs beside `gate_probe` and the
adversarial suite, never in place of them: a skill can influence an agent but
cannot enforce a boundary it can ignore.

### Other systems, in one line each

- [OpenHands](https://docs.openhands.dev/openhands/usage/architecture/runtime)
  is a credible sandbox/runtime source: Docker isolation helps resource control
  and reproducibility, but does not make worker-produced numbers trusted.
- [SWE-agent](https://github.com/SWE-agent/SWE-agent) usefully separates
  trajectories from external evaluation and now recommends its smaller
  mini-SWE-agent for most new work; it is a research harness, not a durable
  governance plane.
- [Factory's public action](https://github.com/Factory-AI/droid-action) is useful
  as a narrow review-action shape, but Factory's public repository is documentation
  rather than Droid's runtime, so it is not evidence for a reviewable TCB.
- [Codex safety guidance](https://openai.com/index/running-codex-safely/) confirms
  the default-deny, isolated-workspace direction already taken here. It concerns
  safe execution, not empirical result validity.

---

## Addendum, 2026-07-28: HumanLayer, and where an approval stops being advice

Read after the code review in
[`docs/reference/code-review-insights-2026-07-28.html`](../reference/code-review-insights-2026-07-28.html),
which asks the sharpest version of this survey's question: *which control is
enforced outside the agent that benefits from bypassing it?*

HumanLayer is the obvious place to test that, because approval-before-action is
the entire product rather than a hardening feature bolted onto a coding agent.
It is also the one system here whose name is a claim about the trust boundary.

| Repo | ★ | What was read |
|---|---|---|
| [humanlayer/humanlayer](https://github.com/humanlayer/humanlayer) | 11.2k | `hld/mcp/server.go`, `hld/approval/manager.go`, `hld/rpc/handlers.go`, `hld/store/errors.go` |
| [humanlayer/agentcontrolplane](https://github.com/humanlayer/agentcontrolplane) | 445 | `acp/internal/controller/toolcall/toolcall_controller.go`, `README.md` §"Incorporating Human Approval" |
| [12-factor-agents](https://github.com/humanlayer/12-factor-agents) | 24.9k | Methodology, not a runtime. Not evidence for a boundary. |

**First, a fact that changes how to read the star counts.** The 11.2k-star
`humanlayer` SDK is gone. Its README now says the code is "pretty much all
deprecated" and points at the hosted product. What lives in that repo today is
CodeLayer: a daemon (`hld`), a CLI (`hlyr`), and a desktop UI. Citing
"HumanLayer, 11k stars" as evidence for an approval library is citing a
tombstone.

### The finding: the same company shipped both shapes, and they are opposites

**CodeLayer's approval is advisory.** `request_approval` is registered as an
*MCP tool the agent calls* (`hld/mcp/server.go:60-61`). The daemon blocks on a
channel, and when the human decides it returns a JSON body — `{"behavior":
"allow", "updatedInput": ...}` or `{"behavior": "deny"}`. The daemon never
executes the tool and never holds a credential for it. Enforcement is Claude
Code honouring that string. An agent that declines to call `request_approval`,
or that ignores a `deny`, is not stopped by anything in this repository.

**ACP's approval is a boundary.** `toolcall_controller.go` is an explicit state
machine whose phases are separate reconciler methods: `handleCheckApproval` →
`handleWaitForApproval` → `handleExecute` (lines 138, 146, 154), with
`ToolCallPhaseAwaitingHumanApproval` as a durable phase on the CRD. The
*controller* executes the tool after the transition. The agent proposes; the
operator disposes. `kubectl get toolcall` showing `AwaitingHumanApproval` is the
boundary being visible from outside the agent's process.

This is invariant 9 for the seventh time, and the first time both sides of it
appear inside one organisation. It is not a criticism of CodeLayer: its threat
model is an overeager assistant at a developer's keyboard, not an adversary, and
against that threat an advisory prompt is the right cost. It is the wrong shape
here for the same reason Symphony's is, and the reason is worth stating in the
form the code review used — **an approval that the approved party enforces is a
verdict returned by the producer.**

### `DangerouslySkipPermissions`, and giving credit where it is due

`hld/rpc/handlers.go:556-585` auto-approves *every pending approval* the moment
bypass mode is enabled, and `hld/approval/manager.go:304` lets bypass mode
override the per-tool path entirely. Three things about this are right and worth
copying rather than sneering at:

1. **The name carries the warning.** `DangerouslySkipPermissions`, not
   `autoApprove`. Compare `--dangerously-skip-permissions`.
2. **It expires.** `DangerouslySkipPermissionsExpiresAt` is checked at
   `manager.go:306` and self-disables. A bypass with no clock is a bypass
   forever.
3. **It is loud.** Every auto-approval emits a `slog.Info` naming the approval
   id and session.

One caveat found by reading rather than by the docs: the expiry is evaluated
**lazily, only when the next approval is created**. There is no sweeper. A
session that goes quiet stays nominally in bypass until something asks. That is
harmless there and would not be harmless in a ledger.

Also worth taking: `store/errors.go` defines `ErrAlreadyDecided` so an approval
is single-decision by construction, and `MCP_AUTO_DENY_ALL` gives the test lane a
fail-closed switch. Both are cheap. `ErrAlreadyDecided` is the shape
`holdout.py` already wants for a spent budget.

### Consequence for M2-03 / BRE-16 — the first real durable-queue answer

Symphony §14.3 says scheduler state is deliberately in-memory and a restart
orphans a long job. ACP answers exactly that, and its README names the mechanism:
"async/await at the infrastructure layer, checkpointing a conversation chain
whenever a tool call or agent delegation occurs." State is CRDs in etcd, so it
survives operator restart by construction.

**Correction, made the same day this section was written.** The first draft
dismissed ACP as "Kubernetes and etcd to schedule one RTX 4070." That reasons
from the wrong denominator and the error is worth recording, because it is the
same conflation the rest of this file makes in places.

**The GPU is not the factory.** It is one slice: the workloads that arrive
needing model training. Most software work that lands here needs no GPU at all,
and for the work that does, compute is deliberately pluggable — edge, local GPU,
or infra compute (TBA). C-01 said this already ("local GPU now, edge or external
later"); the 4070 is today's cheapest instance of one slice, not the thing being
designed around.

This is not a new position, which is the embarrassing part.
[`factory-chart.html`](../reference/factory-chart.html) already calls it "the
split that shapes everything" and warns in the same breath that "conflating them
is the failure mode that sinks this": a **deterministic** lane (web, iOS, infra,
sim harness — ticket → PR, CI is ground truth, "gh-aw + CI already solves this
well") and an **empirical** lane (ML research, hill-climbing, sensor fusion —
experiment → adjudicated result, "nothing off-the-shelf"). The GPU belongs to
the second lane only. Reasoning about factory-wide infrastructure from the size
of one card is the conflation the chart names, committed in the file that
surveys everyone else for committing it.

Two consequences, and they point the opposite way from the draft:

1. **Durable execution is a factory-wide requirement, not an ML one.** A long
   test matrix, a migration, a crawl, a build, and a six-hour training run all
   fail the same way when the orchestrator restarts. Sizing the durable queue to
   the GPU slice understates it. This *raises* BRE-16 rather than parking it.
2. **The reservation protocol must be substrate-agnostic.** `ComputeSubstrate`
   is already a `Protocol`; the durable intent → idempotency key → handle
   binding from the code review's finding 3 belongs in `JobRegistry` on the
   registry side of that seam, identical across edge, local and infra. Likewise
   finding 2's pricing function is keyed by *substrate rate card*, not by GPU
   SKU — the three targets have three different cost shapes and one of them is
   not yet chosen.

So: adopt the **shape** — a phase made durable *before* the side effect, with
the executor reading it back, which is exactly what `handleCheckApproval` →
`handleWaitForApproval` → `handleExecute` encodes and exactly what finding 3
asks for. Whether the deployment is Kubernetes is a separate question that
scales with where compute actually lands, and it is **deferred until infra
compute is chosen, not declined**. M2-02's "adopt no orchestrator codebase"
still stands and is not what this touches.

BRE-16 can no longer say a durable queue does not exist upstream. It does.

### Consequence for BRE-18 — checked against the live API, not the docs

The Linear token was set this session and the identity question answered by
querying rather than reasoning:

```
viewer { name: "Brett R", email: bmr070@gmail.com, isMe: true }
organization { name: "brett", urlKey: "biosun" }
teams { BRE }
```

A `lin_api_` personal key **is the issuing human**. It cannot satisfy invariant 7,
because `label_actor` would see `actor: Brett R, botActor: null` for a label the
runner applied to itself. Confirmed against real history: every `IssueHistory`
node on BRE-18 and BRE-21 today reads exactly that way. The `actor`/`botActor`
split `linear_tracker.py` relies on is real and present in the schema, but it has
never been exercised against an actual bot actor here, because nothing but a
human has ever written to this workspace.

So BRE-18's "the Linear one is free" still holds and the ticket is still an
account action. What is new is that the free thing is **not** a personal API key.
It is an OAuth application with `actor=app`, which is what populates `botActor`.
A personal key is the trap, and it is the one sitting in `.env` right now.

**Unrelated but confirmed by the same query:** `issues(first:3)` returned
`pageInfo.hasNextPage: true` on a workspace with one team and a handful of
tickets. The code review's finding 5 — that first-page-only reads are not a
durable work queue — is not a future concern. It is already true here.

### Method note, again

The deprecation notice, the advisory-vs-enforced split, and the lazy expiry are
all invisible from the READMEs and the product pages; two of them contradict what
the marketing implies. The previous addendum's rule held for a third time:
**search the repo, not the README.** The corollary this pass adds: when a system's
name asserts a boundary, go find the line that executes the action, and see who
owns the process it runs in.

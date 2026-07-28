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

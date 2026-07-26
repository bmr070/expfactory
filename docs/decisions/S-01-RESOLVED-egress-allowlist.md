---
id: S-01
parent: wayfinder:map
labels: [wayfinder:decision]
mode: HITL
status: RESOLVED
raised: 2026-07-26
resolved: 2026-07-26
---

# S-01 — Egress policy versus dataset downloads

## The tension

Map I flagged this as the residual security risk and left it open: the sandbox
is **default-deny outbound** (W-04, W-06), and the workload needs to pull weights
and datasets from HuggingFace.

The reason it stayed open is that every loosening of default-deny is a hole an
agent can be steered through, and invariant 7 already names the attack shape —
the tracker is untrusted input, so a ticket naming a "dataset mirror" is exactly
how someone widens this.

## Decision

**Allowlist specific hosts in code, pin every artifact by SHA-256, and provide no
runtime path to widen either.** GH#7's proposed resolution, implemented.

### The allowlist is code, not configuration

`ALLOWED_HOSTS` is a `frozenset` in `src/expfactory/egress.py`, which is in
`_HARNESS_PATHS` and CODEOWNERS. There is deliberately **no environment
variable, no config file, and no runtime API** to extend it.

That is the whole control. "Human-maintained" only means something if the agent
cannot do the maintaining, and inside a sandbox the agent can set environment
variables and write config files. It cannot merge a pull request. A test parses
the module and fails if it ever imports `os` or reads `environ`/`getenv`.

### Exact hosts, never suffixes

Three ways a suffix rule fails, all now fixtures:

| URL | why a naive rule accepts it |
| -- | -- |
| `https://evil-huggingface.co/…` | `endswith("huggingface.co")` is true |
| `https://huggingface.co.evil.example/…` | substring match is true |
| `https://huggingface.co@evil.example/…` | reaches evil.example; the friendly text is userinfo |

Subdomains are listed individually, because "we need one subdomain" and "we
trust every subdomain forever" are different claims and only the first is
usually true. Credentials in a URL are refused outright, including when the real
host *is* allowlisted — there is no legitimate use here, and permitting it would
make the policy depend on every future reader parsing userinfo correctly.

### Host matching and checksums are both required

An allowlisted host is still a third party that can serve different bytes
tomorrow. Host matching is a claim about the envelope; the pin is the claim about
the contents. Neither alone is the control:

- pin without host matching → the request itself leaks what is being pulled, and
  plain HTTP would make the pin the only integrity check;
- host matching without a pin → "we only download from trusted domains", which is
  not an integrity guarantee.

Size is checked before hashing, so a hostile multi-gigabyte body is refused
without being read end to end.

### Refusals raise

`check_url` raises `EgressRefused` rather than returning a bool. A boolean
invites `if allowed(url):` with no else, and a policy check that can be defeated
by forgetting to read its result is not a policy check. `EgressRefused` subclasses
`PermissionError` so that code catching `OSError` for transient network faults
does not silently swallow a policy decision as a retryable error.

The refusal text names the remedy — a reviewed pull request — because a message
that reads like a transient failure invites a retry loop or a "temporary"
override.

## What this does not do

- **It makes no network requests.** It answers "is this permitted" and "are these
  the right bytes". Enforcement at the socket level belongs to the sandbox, and
  this is the policy that sandbox should be configured from, not a replacement
  for it.
- **It does not follow redirects.** A redirect from an allowlisted host to an
  unlisted one must be re-checked by whatever performs the fetch. Recorded as a
  gap rather than assumed away: `cdn-lfs.huggingface.co` is on the list precisely
  because HuggingFace blob downloads redirect there, and the caller has to
  re-check each hop.

## Consequences

- 295 tests (was 269). Most of the new ones are host-confusion attacks, because
  that is where the bugs are.
- `egress.py` joins the protected set: a diff that widens the allowlist is
  exactly the thing a human must look at, so the substrate guard blocking on it
  is correct rather than inconvenient.
- A tripwire test fails if the allowlist grows past six hosts. Not a functional
  limit — egress rules grow quietly, and a diff that doubles the list should
  cost a conversation.

## Not decided here

- **Redirect re-checking in the fetcher.** The policy supports it; nothing
  implements a fetcher yet.
- **Whether the sandbox enforces this at the socket layer.** It should. That is a
  provisioning task against a real runtime, not a code change here.

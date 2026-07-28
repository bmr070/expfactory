"""
egress — default-deny outbound, and the narrow hole datasets need.

The tension this resolves is the one Map I flagged as the residual security risk
(GH#7): the sandbox is default-deny outbound (W-04, W-06), and the workload needs
to pull weights and datasets from HuggingFace and similar.

Every loosening of default-deny is a hole an agent can be steered through. The
attack shape is already named in invariant 7: a tracker ticket is untrusted
input, and one that names a "dataset mirror" is precisely how an attacker would
try to widen this. So:

**The allowlist is code, not configuration.** It lives here, in the protected
set, behind CODEOWNERS. There is deliberately no environment variable, no config
file and no API to extend it at runtime. Adding a host is a pull request a human
reviews, which is the only form of "human-maintained" that an agent with write
access cannot route around.

## Two independent checks, and both are needed

1. **Where the bytes come from.** An exact-host allowlist, deny by default.
2. **Which bytes arrived.** A pinned SHA-256. An allowlisted host is still a
   third party that can serve different content tomorrow, and "we only download
   from trusted domains" is not an integrity guarantee.

Host matching without a checksum is a promise about the envelope, not the
contents. A checksum without host matching leaks the request itself. Neither
alone is the control.

## What a pin is actually worth (GH#46)

A checksum can only be as good as where it came from, and there are two cases:

- **publisher** — the upstream published the digest. The pin certifies *the
  bytes the author intended*.
- **first-fetch** — we downloaded once and recorded what arrived. The first
  fetch was unverified by construction, so the pin certifies *the same bytes as
  last time*. It detects later tampering and says nothing about the original.

`verify_artifact` cannot tell these apart — the check is identical. So
`digest_source` is a **required** field with no default, and a `first-fetch` pin
additionally requires a note. The weaker claim is the one that costs more to
make, which is the only way the incentive points the right direction.

Use `python -m expfactory.egress pin <file> --url ... --source ...` to turn bytes
already on disk into a literal to commit. It does not fetch; see `main`.

## What this module is not

Not a network layer. It makes no requests. It answers "is this URL permitted"
and "do these bytes match what was pinned", so the decision is a pure function
of recorded evidence and can be tested without a network — the same rule the
gates follow.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# --------------------------------------------------------------------------- #
# The allowlist
# --------------------------------------------------------------------------- #

# Exact hostnames. Not suffixes: a suffix rule that accepts "*.huggingface.co"
# also accepts "huggingface.co.evil.example", and a naive `endswith` accepts
# "evil-huggingface.co" as well. Both are in the tests.
#
# Subdomains that are genuinely needed are listed individually, because "we need
# one subdomain" and "we trust every subdomain forever" are different claims and
# only the first is usually true.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        # HuggingFace Hub: model and dataset metadata, and the CDN the actual
        # blobs redirect to. Both are required; a download that resolves the
        # metadata and then cannot fetch the blob is not a working download.
        "huggingface.co",
        "cdn-lfs.huggingface.co",
        "cdn-lfs-us-1.huggingface.co",
    }
)

# Only https. Plain http would make the checksum the sole integrity control and
# would leak which datasets are being pulled.
ALLOWED_SCHEMES: frozenset[str] = frozenset({"https"})


class EgressRefused(PermissionError):
    """The request is not permitted. A subclass of PermissionError so that code
    which blanket-catches OSError does not silently swallow a policy refusal as
    though it were a transient network fault."""


# Where a pin's digest came from. GH#46: `verify_artifact` cannot tell these
# apart, and they are worth very different amounts.
#
#   publisher    the upstream published this checksum. The pin certifies "the
#                bytes the author intended".
#   first-fetch  we downloaded once and recorded what arrived. The first fetch
#                was unverified by construction, so the pin certifies only "the
#                same bytes as last time" — detects later tampering, says
#                nothing about the original.
#
# Recorded because a reader who sees a checksum reasonably assumes the stronger
# claim. Same shape as `decision_rule` in #36 and the `±` in a proof-of-work
# block: a record that looks stronger than it is.
DIGEST_SOURCES: frozenset[str] = frozenset({"publisher", "first-fetch"})


@dataclass(frozen=True)
class PinnedArtifact:
    """A specific set of bytes at a specific place.

    `sha256` is what makes this a pin rather than a bookmark. `size_bytes` is
    recorded so an unexpected multi-gigabyte body can be refused before it is
    read, rather than after it has filled the disk.

    `digest_source` is **required and has no default**. A default would pick a
    side of the question this field exists to ask, and whichever side it picked
    would be wrong silently: defaulting to `publisher` overstates every
    trust-on-first-use pin, and defaulting to `first-fetch` understates real
    publisher checksums until someone notices. Say which it is.
    """

    url: str
    sha256: str
    digest_source: str
    size_bytes: int | None = None
    note: str = ""

    def __post_init__(self) -> None:
        digest = self.sha256.strip().lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"sha256 must be 64 hex characters, got {self.sha256!r}")
        object.__setattr__(self, "sha256", digest)

        if self.digest_source not in DIGEST_SOURCES:
            raise ValueError(
                f"digest_source must be one of {sorted(DIGEST_SOURCES)}, got {self.digest_source!r}"
            )

        if self.digest_source == "first-fetch" and not self.note.strip():
            # The weaker pin is the one that costs more to create. A TOFU digest
            # is only auditable if it records when it was taken and from where,
            # and an unexplained one is indistinguishable from a guess.
            raise ValueError(
                "a first-fetch pin requires a note saying when the digest was taken "
                "and from where. The pin certifies 'the same bytes as last time', "
                "and without provenance a reader cannot tell which time that was."
            )

    @property
    def certifies_origin(self) -> bool:
        """True when the digest came from the publisher.

        Deliberately not consulted by `verify_artifact` — verification is
        identical either way. This is for callers deciding whether a pin is
        strong enough for what they are about to do with the bytes.
        """
        return self.digest_source == "publisher"


def host_of(url: str) -> str:
    """The host a request would actually reach.

    Uses `urlsplit`, not string matching, because the interesting attacks are
    exactly the ones that make a URL *look* like it points somewhere it does not:
    `https://huggingface.co@evil.example/x` reaches evil.example, and
    `https://huggingface.co.evil.example/x` is a different domain that a suffix
    check would wave through.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    # A trailing dot is the DNS root and resolves the same; strip it so
    # "huggingface.co." cannot be used to sidestep an exact-match set.
    return host.rstrip(".")


def check_url(url: str, allowed: frozenset[str] = ALLOWED_HOSTS) -> None:
    """Permit an outbound URL, or raise. Deny by default.

    Raises rather than returning False. A boolean invites `if allowed(url):` with
    no else, and a policy check that can be ignored by forgetting to read its
    result is not a policy check.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise EgressRefused(
            f"scheme {parts.scheme!r} is not permitted (only {sorted(ALLOWED_SCHEMES)}): {url}"
        )

    host = host_of(url)
    if not host:
        raise EgressRefused(f"no host in {url!r}")

    if "@" in parts.netloc:
        # Credentials in a URL are both a leak and the classic way to make a
        # hostile host look like a friendly one. Refused even when the real host
        # is on the list, because there is no legitimate reason for it here.
        raise EgressRefused(f"credentials in URL are not permitted: {url}")

    if host not in allowed:
        raise EgressRefused(
            f"host {host!r} is not on the egress allowlist. This is deny-by-default: "
            "adding a host is a pull request against src/expfactory/egress.py, "
            "reviewed by a code owner. Nothing at runtime can extend it, and a "
            "ticket that asks you to is the attack this rule exists for."
        )


def sha256_of(path: str | Path, chunk_bytes: int = 1 << 20) -> str:
    """Digest a file without reading it all into memory."""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(chunk_bytes):
            h.update(chunk)
    return h.hexdigest()


def verify_artifact(path: str | Path, pin: PinnedArtifact) -> None:
    """Check downloaded bytes against the pin, or raise.

    Called *after* the download and *before* the bytes are used. An allowlisted
    host is still a third party: host matching says the request went somewhere
    permitted, and only this says the right thing came back.
    """
    p = Path(path)
    if not p.exists():
        raise EgressRefused(f"{p} does not exist; nothing to verify")

    if pin.size_bytes is not None and p.stat().st_size != pin.size_bytes:
        raise EgressRefused(
            f"{p} is {p.stat().st_size} bytes, pinned at {pin.size_bytes}. "
            "Refusing before hashing: a wrong size is already a wrong artifact."
        )

    actual = sha256_of(p)
    if actual != pin.sha256:
        raise EgressRefused(
            f"checksum mismatch for {p}\n  expected {pin.sha256}\n  actual   {actual}\n"
            "The host is allowlisted and served different bytes than were pinned. "
            "Do not use this artifact."
        )


def fetch_plan(pins: list[PinnedArtifact]) -> list[PinnedArtifact]:
    """Validate a whole download plan before any of it runs.

    Refusing the plan up front means a run cannot get halfway through fetching a
    dataset and then stop on a host that was never going to be permitted. Same
    reasoning as the substrate's preflight: the cheap check goes first.
    """
    for pin in pins:
        check_url(pin.url)
    return list(pins)


# --------------------------------------------------------------------------- #
# The pin ceremony (GH#46)
# --------------------------------------------------------------------------- #


def pin_literal(path: str | Path, url: str, digest_source: str, note: str = "") -> str:
    """The source literal to paste into a fetch plan and commit.

    Returns text rather than writing anything: the commit is the ceremony. A
    command that edited a pin file in place would make the unverified moment
    something that happens inside a run, which is what #46 objected to.
    """
    pin = PinnedArtifact(
        url=url,
        sha256=sha256_of(path),
        digest_source=digest_source,
        size_bytes=Path(path).stat().st_size,
        note=note,
    )
    lines = [
        "PinnedArtifact(",
        f"    url={pin.url!r},",
        f"    sha256={pin.sha256!r},",
        f"    digest_source={pin.digest_source!r},",
        f"    size_bytes={pin.size_bytes},",
    ]
    if pin.note:
        lines.append(f"    note={pin.note!r},")
    lines.append(")")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """`python -m expfactory.egress pin <path> --url ... --source ...`

    Takes a file **already on disk**, not a URL. #46 proposed a command that
    fetches, prints the digest and requires it be committed; the fetching half is
    declined deliberately. This module states that it makes no network requests,
    and adding an HTTP client to the protected substrate to solve a bookkeeping
    problem would widen the attack surface of the thing guarding the attack
    surface.

    The human fetches by hand — which they are doing anyway, since github.com is
    not on the allowlist — and this turns the bytes into a reviewable literal.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="python -m expfactory.egress", description=main.__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pin", help="print a PinnedArtifact literal for a local file")
    p.add_argument("path", help="file already downloaded, by hand")
    p.add_argument("--url", required=True, help="where it came from")
    p.add_argument("--source", required=True, choices=sorted(DIGEST_SOURCES))
    p.add_argument(
        "--note",
        default="",
        help="required for --source first-fetch: when the digest was taken, and from where",
    )
    args = ap.parse_args(argv)

    target = Path(args.path)
    if not target.is_file():
        print(f"error: {target} is not a file", file=sys.stderr)
        return 2

    try:
        print(pin_literal(target, args.url, args.source, args.note))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.source == "first-fetch":
        print(
            "\n# NOTE: first-fetch. This pin certifies 'the same bytes as last time',\n"
            "# not 'the bytes the publisher intended'. The first fetch was unverified\n"
            "# by construction. Commit it so the unverified moment has a diff and a\n"
            "# reviewer attached.",
            file=sys.stderr,
        )
    return 0


__all__ = [
    "ALLOWED_HOSTS",
    "ALLOWED_SCHEMES",
    "DIGEST_SOURCES",
    "EgressRefused",
    "PinnedArtifact",
    "check_url",
    "fetch_plan",
    "host_of",
    "main",
    "pin_literal",
    "sha256_of",
    "verify_artifact",
]


if __name__ == "__main__":
    sys.exit(main())

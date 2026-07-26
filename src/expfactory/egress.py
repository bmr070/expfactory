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

## What this module is not

Not a network layer. It makes no requests. It answers "is this URL permitted"
and "do these bytes match what was pinned", so the decision is a pure function
of recorded evidence and can be tested without a network — the same rule the
gates follow.
"""

from __future__ import annotations

import hashlib
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


@dataclass(frozen=True)
class PinnedArtifact:
    """A specific set of bytes at a specific place.

    `sha256` is what makes this a pin rather than a bookmark. `size_bytes` is
    recorded so an unexpected multi-gigabyte body can be refused before it is
    read, rather than after it has filled the disk.
    """

    url: str
    sha256: str
    size_bytes: int | None = None
    note: str = ""

    def __post_init__(self) -> None:
        digest = self.sha256.strip().lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"sha256 must be 64 hex characters, got {self.sha256!r}")
        object.__setattr__(self, "sha256", digest)


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


__all__ = [
    "ALLOWED_HOSTS",
    "ALLOWED_SCHEMES",
    "EgressRefused",
    "PinnedArtifact",
    "check_url",
    "fetch_plan",
    "host_of",
    "sha256_of",
    "verify_artifact",
]

"""
Default-deny outbound, and the ways a URL lies about where it goes.

GH#7 was the last unresolved design tension from Map I: the sandbox is
default-deny outbound, and the workload needs weights and datasets from
HuggingFace. Every loosening is a hole an agent can be steered through, and
invariant 7 already names the shape — a tracker ticket is untrusted input, and
one naming a "dataset mirror" is exactly how someone would try to widen this.

Most of these tests are host-confusion attacks, because that is where the bugs
are. `endswith`, `in`, and suffix wildcards each fail at least one of them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from expfactory.egress import (
    ALLOWED_HOSTS,
    EgressRefused,
    PinnedArtifact,
    check_url,
    fetch_plan,
    host_of,
    main,
    pin_literal,
    sha256_of,
    verify_artifact,
)

OK = "https://huggingface.co/datasets/x/resolve/main/data.tar"


# --------------------------------------------------------------------------- #
# The allowlist
# --------------------------------------------------------------------------- #


def test_an_allowlisted_host_passes():
    check_url(OK)
    check_url("https://cdn-lfs.huggingface.co/repos/aa/bb/blob")


def test_an_unknown_host_is_refused():
    with pytest.raises(EgressRefused, match="not on the egress allowlist"):
        check_url("https://example.com/data.tar")


def test_the_refusal_explains_that_runtime_cannot_widen_it():
    """A refusal that reads as a transient failure invites a retry loop or a
    'temporary' override. This one has to say what the actual remedy is."""
    with pytest.raises(EgressRefused) as exc:
        check_url("https://example.com/data.tar")
    assert "pull request" in str(exc.value)


# ---- host confusion --------------------------------------------------------


def test_a_lookalike_prefix_is_refused():
    """`endswith("huggingface.co")` accepts this. It is a different domain owned
    by whoever registered it."""
    with pytest.raises(EgressRefused):
        check_url("https://evil-huggingface.co/data.tar")


def test_a_lookalike_suffix_is_refused():
    """A suffix wildcard for `*.huggingface.co` written as a substring check
    accepts this too."""
    with pytest.raises(EgressRefused):
        check_url("https://huggingface.co.evil.example/data.tar")


def test_credentials_in_the_url_are_refused():
    """`https://huggingface.co@evil.example/x` reaches evil.example. The text
    before the `@` is userinfo, and it is there to fool a human reading the URL
    in a ticket."""
    with pytest.raises(EgressRefused, match="credentials"):
        check_url("https://huggingface.co@evil.example/data.tar")


def test_credentials_are_refused_even_when_the_real_host_is_allowed():
    """No legitimate use here, and permitting it would mean the allowlist depends
    on parsing userinfo correctly in every future reader of these URLs."""
    with pytest.raises(EgressRefused, match="credentials"):
        check_url("https://user:token@huggingface.co/data.tar")


def test_a_trailing_dot_does_not_sidestep_exact_matching():
    """`huggingface.co.` is the fully-qualified form and resolves identically, so
    it must be treated as the same host rather than as an unknown one — and, more
    importantly, must not be usable to bypass a set membership test."""
    assert host_of("https://huggingface.co./x") == "huggingface.co"
    check_url("https://huggingface.co./x")


def test_case_is_not_significant_in_a_hostname():
    check_url("https://HuggingFace.CO/datasets/x")


def test_an_undeclared_subdomain_is_refused():
    """Subdomains are listed individually on purpose. "We need one subdomain" and
    "we trust every subdomain forever" are different claims."""
    with pytest.raises(EgressRefused):
        check_url("https://sneaky.huggingface.co/data.tar")


def test_plain_http_is_refused():
    """Without TLS the checksum becomes the only integrity control, and the
    request itself leaks which datasets are being pulled."""
    with pytest.raises(EgressRefused, match="scheme"):
        check_url("http://huggingface.co/data.tar")


def test_a_non_web_scheme_is_refused():
    for url in ("file:///etc/passwd", "ftp://huggingface.co/x", "data:text/plain,hi"):
        with pytest.raises(EgressRefused):
            check_url(url)


def test_a_url_with_no_host_is_refused():
    with pytest.raises(EgressRefused):
        check_url("https:///just-a-path")


# ---- the allowlist itself --------------------------------------------------


def test_the_allowlist_is_small_and_https_only():
    """Not a functional check — a tripwire. Egress rules grow quietly, and a
    diff that doubles this list should be something a reviewer has to look at."""
    assert len(ALLOWED_HOSTS) <= 6, "the egress allowlist has grown; justify each host"


def test_no_host_carries_a_scheme_or_path():
    """A stray "https://" in the set silently matches nothing, and a rule that
    matches nothing looks identical to a rule that is working."""
    for host in ALLOWED_HOSTS:
        assert "/" not in host and ":" not in host, f"{host!r} is not a bare hostname"
        assert host == host.lower().strip("."), f"{host!r} is not normalised"


def test_the_allowlist_cannot_be_widened_at_runtime():
    """The control is that adding a host is a code review. A frozenset cannot be
    mutated by anything that gets a reference to it, including an agent."""
    with pytest.raises(AttributeError):
        ALLOWED_HOSTS.add("evil.example")  # type: ignore[attr-defined]


def test_there_is_no_environment_override():
    """Deliberately absent. An env var would move the control from 'a human
    reviewed a diff' to 'whoever can set an environment variable', which inside a
    sandbox is the agent.

    Parsed rather than grepped: the first version of this test searched the raw
    source for "environ" and tripped on the module docstring explaining that
    there is no environment override. A prose match is not a code check.
    """
    import ast

    source = (
        Path(__file__).resolve().parent.parent / "src" / "expfactory" / "egress.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    reads: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
            reads.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in ("environ", "getenv"):
            reads.append(node.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names]
            assert "os" not in names, "egress.py must not import os; it reads no configuration"

    assert not reads, f"egress.py reads the environment: {reads}"


# --------------------------------------------------------------------------- #
# Pinning
# --------------------------------------------------------------------------- #


def _write(tmp_path: Path, body: bytes) -> tuple[Path, str]:
    p = tmp_path / "artifact.bin"
    p.write_bytes(body)
    return p, hashlib.sha256(body).hexdigest()


def test_matching_bytes_verify(tmp_path: Path):
    p, digest = _write(tmp_path, b"weights")
    verify_artifact(p, PinnedArtifact(url=OK, sha256=digest, digest_source="publisher"))


def test_an_allowlisted_host_serving_different_bytes_is_caught(tmp_path: Path):
    """The reason host matching alone is not the control. The domain is trusted;
    the bytes are a third party's and can change."""
    p, _ = _write(tmp_path, b"not what was pinned")
    expected = hashlib.sha256(b"what was pinned").hexdigest()

    with pytest.raises(EgressRefused, match="checksum mismatch"):
        verify_artifact(p, PinnedArtifact(url=OK, sha256=expected, digest_source="publisher"))


def test_a_wrong_size_is_refused_before_hashing(tmp_path: Path):
    """Cheaper, and it means a hostile multi-gigabyte body is rejected without
    being read end to end."""
    p, digest = _write(tmp_path, b"weights")
    with pytest.raises(EgressRefused, match="bytes, pinned at"):
        verify_artifact(
            p,
            PinnedArtifact(url=OK, sha256=digest, digest_source="publisher", size_bytes=999_999),
        )


def test_a_missing_file_is_refused_not_silently_ok(tmp_path: Path):
    with pytest.raises(EgressRefused, match="does not exist"):
        verify_artifact(
            tmp_path / "nope.bin",
            PinnedArtifact(url=OK, sha256="ab" * 32, digest_source="publisher"),
        )


def test_a_malformed_digest_is_rejected_at_construction():
    """A pin with a typo'd digest would fail every verification, which reads as a
    network problem rather than a bad pin."""
    for bad in ("", "abc", "z" * 64, "AB" * 31):
        with pytest.raises(ValueError, match="64 hex"):
            PinnedArtifact(url=OK, sha256=bad, digest_source="publisher")


def test_a_digest_is_normalised_to_lowercase():
    pin = PinnedArtifact(url=OK, sha256="AB" * 32, digest_source="publisher")
    assert pin.sha256 == "ab" * 32


def test_sha256_of_matches_hashlib(tmp_path: Path):
    """Guards the guard: chunked hashing that quietly read nothing would make
    every verification pass."""
    p, digest = _write(tmp_path, b"x" * (3 << 20))  # larger than one chunk
    assert sha256_of(p) == digest


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


def test_a_plan_is_refused_whole_if_any_url_is(tmp_path: Path):
    """A run that fetches half a dataset and then stops on a host that was never
    going to be permitted has spent time and bandwidth to learn something the
    cheap check knew up front."""
    pins = [
        PinnedArtifact(url=OK, sha256="ab" * 32, digest_source="publisher"),
        PinnedArtifact(url="https://example.com/x", sha256="cd" * 32, digest_source="publisher"),
    ]
    with pytest.raises(EgressRefused):
        fetch_plan(pins)


def test_a_clean_plan_passes_through():
    pins = [PinnedArtifact(url=OK, sha256="ab" * 32, digest_source="publisher")]
    assert fetch_plan(pins) == pins


# --------------------------------------------------------------------------- #
# What a pin is worth — GH#46
# --------------------------------------------------------------------------- #


def test_a_pin_must_say_where_its_digest_came_from():
    """The bootstrap problem, made visible. `verify_artifact` cannot tell a
    publisher checksum from one we recorded off our own first download, and the
    two are worth different amounts — so the record has to say."""
    with pytest.raises(TypeError):
        PinnedArtifact(url=OK, sha256="ab" * 32)  # type: ignore[call-arg]


def test_an_unknown_digest_source_is_refused():
    with pytest.raises(ValueError, match="digest_source must be one of"):
        PinnedArtifact(url=OK, sha256="ab" * 32, digest_source="vibes")


def test_a_first_fetch_pin_must_explain_itself():
    """The weaker claim is the one that costs more to make. A trust-on-first-use
    digest with no provenance is indistinguishable from a guess, and the
    incentive has to point away from the cheap-looking option."""
    with pytest.raises(ValueError, match="requires a note"):
        PinnedArtifact(url=OK, sha256="ab" * 32, digest_source="first-fetch")

    ok = PinnedArtifact(
        url=OK,
        sha256="ab" * 32,
        digest_source="first-fetch",
        note="fetched by hand 2026-07-27 from the upstream release page",
    )
    assert not ok.certifies_origin


def test_a_publisher_pin_needs_no_note_and_claims_origin():
    pin = PinnedArtifact(url=OK, sha256="ab" * 32, digest_source="publisher")
    assert pin.certifies_origin


def test_verification_does_not_care_where_the_digest_came_from(tmp_path: Path):
    """Provenance is a fact about the *record*, not about the bytes. Making
    verification weaker for a first-fetch pin would be the wrong lesson: the
    check is exact either way, it is the claim that differs."""
    p = tmp_path / "a.bin"
    p.write_bytes(b"payload")
    digest = hashlib.sha256(b"payload").hexdigest()

    verify_artifact(p, PinnedArtifact(url=OK, sha256=digest, digest_source="publisher"))
    verify_artifact(
        p,
        PinnedArtifact(url=OK, sha256=digest, digest_source="first-fetch", note="taken 2026-07-27"),
    )


# --------------------------------------------------------------------------- #
# The pin ceremony
# --------------------------------------------------------------------------- #


def test_the_ceremony_emits_a_literal_that_round_trips(tmp_path: Path):
    """What it prints has to be what you can commit. A ceremony that produced
    something needing hand-editing would get hand-edited."""
    p = tmp_path / "d.bin"
    p.write_bytes(b"some bytes")

    literal = pin_literal(p, OK, "publisher")
    rebuilt = eval(literal, {"PinnedArtifact": PinnedArtifact})  # noqa: S307

    assert rebuilt.sha256 == sha256_of(p)
    assert rebuilt.size_bytes == len(b"some bytes")
    assert rebuilt.digest_source == "publisher"


def test_the_ceremony_refuses_a_first_fetch_pin_with_no_note(tmp_path: Path, capsys):
    p = tmp_path / "d.bin"
    p.write_bytes(b"x")

    code = main(["pin", str(p), "--url", OK, "--source", "first-fetch"])

    assert code == 2
    assert "requires a note" in capsys.readouterr().err


def test_the_ceremony_says_out_loud_what_a_first_fetch_pin_is_not(tmp_path: Path, capsys):
    p = tmp_path / "d.bin"
    p.write_bytes(b"x")

    code = main(
        ["pin", str(p), "--url", OK, "--source", "first-fetch", "--note", "by hand 2026-07-27"]
    )
    out = capsys.readouterr()

    assert code == 0
    assert "PinnedArtifact(" in out.out
    assert "not 'the bytes the publisher intended'" in out.err


def test_the_ceremony_does_not_fetch():
    """#46 proposed a command that downloads. Declined: this module states it
    makes no network requests, and an HTTP client inside the protected substrate
    would widen the attack surface of the thing guarding the attack surface.

    Asserted against the parsed source rather than by grepping text, because an
    earlier test in this file matched its own docstring.
    """
    import ast
    import inspect

    from expfactory import egress

    tree = ast.parse(inspect.getsource(egress))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert not imported & {"urllib3", "requests", "httpx", "socket", "http"}

    # `urllib` IS imported — for `urlsplit`, which parses rather than fetches.
    # So name the fetching half and assert it is absent, rather than banning the
    # package wholesale and having to except the one legitimate use.
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } | {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"urlopen", "urlretrieve", "request", "get", "post"}

"""The secret scanner has to catch the shapes that actually got committed.

Two live credentials were sitting in this repository when the scanner was
written — a Fork API key in a recovery runbook and a Render key hardcoded in
a scheduled-task script. A third pair survived the FIRST version of the
scanner, because it was written as

    TOK=rnd_QqJ5...

with no quotes and a variable name (`TOK`) that no secret-name pattern
matches. That near-miss is why the provider-prefix rule exists and why
quotes are optional, and it is pinned here so neither can regress.

These tests exercise the rules directly rather than the repo walk, so they
stay true regardless of what the tree contains.

The `# pragma: allowlist secret` markers below are not a workaround — they
are the scanner's own escape hatch, used on the only lines in the repository
where secret-shaped strings are the point. The moment this file was committed
it became tracked, and the scanner correctly flagged its own fixtures. That
is the mechanism working. Marking each line keeps them visible and greppable
rather than carving out a path exclusion that would silently widen later.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "scan_secrets", Path(__file__).resolve().parents[1] / "scripts" / "scan_secrets.py"
)
scan_secrets = importlib.util.module_from_spec(_SPEC)
sys.modules["scan_secrets"] = scan_secrets
_SPEC.loader.exec_module(scan_secrets)


def _hits(line: str, *, test_path: bool = False):
    """Which rules fire on one line, mirroring scan()'s rule selection."""
    rules = list(scan_secrets.RULES)
    if not test_path:
        rules += scan_secrets.KDF_RULES
    return [name for name, pattern, _ in rules if pattern.search(line)]


# ── the ones that were really in the tree ─────────────────────────────────

MUST_CATCH = [
    # The exact shape that slipped past the first version: bare, unquoted,
    # and assigned to a name no secret-name pattern matches.
    ("TOK=rnd_QqJ5qS97qrfF0IwAVrJhmKpJyNX0", "provider-credential"),  # pragma: allowlist secret
    ("$RenderApiKey   = 'rnd_QqJ5qS97qrfF0IwAVrJhmKpJyNX0'", "provider-credential"),  # pragma: allowlist secret
    ('export FORK_API_KEY="o1JrikYGj4BtJDYGipGlRGfchTwMTLTA6QsT01IXZPQ"',  # pragma: allowlist secret
     "assigned-long-literal"),
    ("aws_access_key_id = AKIAIOSFODNN7EXAMPLE", "aws-access-key-id"),  # pragma: allowlist secret
    ("-----BEGIN RSA PRIVATE KEY-----", "private-key-block"),  # pragma: allowlist secret
    ("-----BEGIN PRIVATE KEY-----", "private-key-block"),  # pragma: allowlist secret
    ("OPENAI_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz012345", "provider-credential"),  # pragma: allowlist secret
    ("GROQ=gsk_abcdefghijklmnopqrstuvwxyz0123", "provider-credential"),  # pragma: allowlist secret
    ("hf_token: hf_abcdefghijklmnopqrstuvwxyz01234567", "provider-credential"),  # pragma: allowlist secret
    ("token = ghp_abcdefghijklmnopqrstuvwxyz0123456789", "provider-credential"),  # pragma: allowlist secret
]


@pytest.mark.parametrize("line,expected_rule", MUST_CATCH)
def test_the_shapes_that_were_committed_are_caught(line, expected_rule):
    fired = _hits(line)
    assert expected_rule in fired, f"{line!r} matched {fired} — expected {expected_rule}"


def test_an_unquoted_assignment_is_not_a_blind_spot():
    """Dotenv and shell lines are bare, and those are the ones pasted into
    runbooks. The first version required quotes and missed them."""
    assert _hits("SECRET_KEY=0123456789abcdef0123456789abcdef")  # pragma: allowlist secret
    assert _hits('SECRET_KEY="0123456789abcdef0123456789abcdef"')  # pragma: allowlist secret


# ── things that must NOT fire, or nobody will run it ──────────────────────

MUST_NOT_CATCH = [
    'SECRET_KEY = os.getenv("SECRET_KEY")',
    'api_key = config["api_key"]',
    'FORK_API_KEY="<FORK_API_KEY -- take it from your .env>"',
    "$RenderApiKey   = $env:RENDER_API_KEY",
    "# Set RENDER_API_KEY in your environment before running this",
    'password = ""',
    "token: str | None = None",
    "--hash=sha256:9f0b1e6d4a2c8f3e5b7d9a1c3e5f7a9b1d3f5a7c9e1b3d5f7a9c1e3b5d7f9a1c",
    '"integrity": "sha512-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH=="',
    "commit 5f09f88a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e",
]


@pytest.mark.parametrize("line", MUST_NOT_CATCH)
def test_ordinary_lines_do_not_fire(line):
    """A scanner with false positives gets ignored, which is worse than
    no scanner at all."""
    assert not _hits(line), f"false positive on {line!r}: {_hits(line)}"


# ── the KDF rules, and why they are non-test-paths only ───────────────────

def test_a_hardcoded_salt_is_caught_in_production_code():
    """The quiet one: it does not look like a password, but it makes every
    derived key reproducible by anyone with the source."""
    assert "hardcoded-salt" in _hits('salt = b"fork-static-salt-v1"')


def test_the_same_salt_is_allowed_in_a_test():
    """A fixed salt is how you make a fixture deterministic."""
    assert "hardcoded-salt" not in _hits('salt = b"fork-static-salt-v1"', test_path=True)


def test_a_fernet_key_built_from_a_literal_is_caught():
    assert "hardcoded-fernet-key" in _hits(
        "f = Fernet('bXlzdXBlcnNlY3JldGZlcm5ldGtleXZhbHVlMTIzNDU2Nzg5MA==')"
    )


# ── the allowlist has to work, or people will delete the check ────────────

def test_an_allowlisted_line_is_ignored_by_the_walk(tmp_path, monkeypatch):
    marked = f"TOK=rnd_QqJ5qS97qrfF0IwAVrJhmKpJyNX0  # {scan_secrets.ALLOW_MARKER}"  # pragma: allowlist secret
    (tmp_path / "runbook.md").write_text(marked, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scan_secrets, "tracked_files", lambda: ["runbook.md"])

    assert scan_secrets.scan() == []


def test_the_walk_reports_an_unmarked_secret(tmp_path, monkeypatch):
    (tmp_path / "runbook.md").write_text(
        "TOK=rnd_QqJ5qS97qrfF0IwAVrJhmKpJyNX0", encoding="utf-8"  # pragma: allowlist secret
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scan_secrets, "tracked_files", lambda: ["runbook.md"])

    findings = scan_secrets.scan()
    assert len(findings) == 1
    assert "runbook.md:1" in findings[0]


def test_the_tree_is_clean_right_now():
    """The gate itself. If this fails, a secret was committed."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "scripts/scan_secrets.py"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"secret material in the tree:\n{result.stdout}"

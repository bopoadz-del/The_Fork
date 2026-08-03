#!/usr/bin/env python3
"""Fail the build if secret material is in the working tree.

Four rules, in descending order of certainty:

  1. AWS access key ids   -- `AKIA` + 16 uppercase alphanumerics. Unambiguous.
  2. Private key blocks   -- `-----BEGIN ... PRIVATE KEY`. Unambiguous.
  3. Long literals assigned to a secret-ish name -- 32+ chars of hex or
     base64 assigned to something called secret/token/key/password/etc.
  4. Hardcoded key-derivation constants on a non-test path -- a literal
     salt, IV or Fernet key. These are the quiet ones: they do not look
     like a password, and they make every derived key reproducible by
     anyone holding the source.

Exit 0 if clean, 1 if anything is found.

WHY IT ONLY SCANS TRACKED FILES: an untracked `.env` on a developer's disk
is where secrets are SUPPOSED to live. Flagging it would train everyone to
ignore this tool. What must never happen is a secret being committed, so
the scan follows `git ls-files` — exactly the set that gets pushed.

To allow a specific line (a documented placeholder, a test fixture key),
put `pragma: allowlist secret` in a comment on that line. Deliberate,
visible, and greppable — unlike a path exclusion that silently widens.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ALLOW_MARKER = "pragma: allowlist secret"

# Binary and vendored paths that would produce noise, not findings.
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".xz",
    ".woff", ".woff2", ".ttf", ".eot", ".onnx", ".bin", ".pyc", ".so",
    ".dll", ".dylib", ".xlsx", ".xls", ".docx", ".ifc", ".mp4", ".wav",
}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
SKIP_DIRS = ("node_modules/", ".venv/", "dist/", "build/", ".worktrees/",
             ".claude/")

SECRET_NAME = r"(?i)\b\w*(secret|token|api[_-]?key|password|passwd|pwd|credential|private[_-]?key)\w*\b"
# 32+ chars of hex, or of base64 alphabet. Quoted literal only — an
# os.getenv() call has no literal to leak.
LONG_HEX = r"[0-9a-fA-F]{32,}"
LONG_B64 = r"[A-Za-z0-9+/_-]{32,}={0,2}"

RULES = [
    (
        "aws-access-key-id",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "AWS access key id",
    ),
    (
        "private-key-block",
        re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
        "private key block",
    ),
    (
        # Provider-issued credentials carry their own prefix. These need NO
        # variable name and NO quotes to be certain, which is what makes them
        # worth their own rule: the first version of this scanner keyed only
        # on the assigned-name rule below and walked straight past a live
        # Render key written as `TOK=rnd_...` in a runbook.
        "provider-credential",
        re.compile(
            r"\b("
            r"rnd_[A-Za-z0-9]{20,}"              # Render
            r"|sk-[A-Za-z0-9_-]{20,}"            # OpenAI and lookalikes
            r"|sk_live_[A-Za-z0-9]{20,}"         # Stripe live
            r"|gh[pousr]_[A-Za-z0-9]{30,}"       # GitHub token
            r"|github_pat_[A-Za-z0-9_]{30,}"     # GitHub fine-grained PAT
            r"|glpat-[A-Za-z0-9_-]{20,}"         # GitLab
            r"|xox[baprs]-[A-Za-z0-9-]{10,}"     # Slack
            r"|AIza[0-9A-Za-z_-]{35}"            # Google API key
            r"|gsk_[A-Za-z0-9]{20,}"             # Groq
            r"|hf_[A-Za-z0-9]{30,}"              # Hugging Face
            r")\b"
        ),
        "provider-issued credential",
    ),
    (
        # Quotes are OPTIONAL here for the same reason: shell and dotenv
        # assignments are bare, and those are exactly the lines that end up
        # pasted into a runbook.
        "assigned-long-literal",
        re.compile(
            SECRET_NAME + r"\s*[:=]\s*[\"']?(" + LONG_HEX + r"|" + LONG_B64 + r")[\"']?"
        ),
        "long literal assigned to a secret-named field",
    ),
]

# Rule 4 — key-derivation material. Only meaningful outside tests, where a
# fixed salt is a legitimate way to make a fixture deterministic.
KDF_RULES = [
    (
        "hardcoded-salt",
        re.compile(r"(?i)\b(salt|iv|nonce)\s*[:=]\s*b?[\"'][^\"']{8,}[\"']"),
        "hardcoded salt/IV/nonce",
    ),
    (
        "hardcoded-fernet-key",
        re.compile(r"Fernet\s*\(\s*[\"'][A-Za-z0-9+/_-]{40,}={0,2}[\"']"),
        "Fernet key built from a literal",
    ),
    (
        "hardcoded-kdf-password",
        re.compile(
            r"(?i)\b(PBKDF2HMAC|pbkdf2_hmac|scrypt|bcrypt\.hashpw)\b[^\n]*"
            r"[\"'][^\"']{8,}[\"']"
        ),
        "key derivation seeded from a literal",
    ),
]


def is_test_path(rel: str) -> bool:
    return rel.startswith("tests/") or "/tests/" in rel or "/test_" in rel \
        or Path(rel).name.startswith("test_") or rel.startswith("scripts/audit_")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def should_scan(rel: str) -> bool:
    if any(rel.startswith(d) or f"/{d}" in rel for d in SKIP_DIRS):
        return False
    p = Path(rel)
    return p.suffix.lower() not in SKIP_SUFFIXES and p.name not in SKIP_NAMES


def scan() -> list[str]:
    findings: list[str] = []
    for rel in tracked_files():
        if not should_scan(rel):
            continue
        path = Path(rel)
        if not path.exists():          # staged deletion
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            print(f"WARN  could not read {rel}: {exc}", file=sys.stderr)
            continue

        rules = list(RULES)
        if not is_test_path(rel):
            rules += KDF_RULES

        for lineno, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARKER in line:
                continue
            for name, pattern, description in rules:
                if pattern.search(line):
                    findings.append(f"{rel}:{lineno}  [{name}] {description}")
                    break
    return findings


def main() -> int:
    findings = scan()
    if findings:
        print("SECRET MATERIAL FOUND:")
        for f in findings:
            print("  " + f)
        print(f"TOTAL: {len(findings)}")
        print(
            "\nIf one of these is a documented placeholder or a test fixture, "
            f"put `{ALLOW_MARKER}` in a comment on that line."
        )
        return 1
    print("NO SECRET MATERIAL IN TRACKED FILES.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Security floors for pinned dependencies.

Exists because the dependency gate (pip-audit) ran report-only for months —
`continue-on-error: true` plus `|| true` meant its green check could never
fail, and HIGH-severity pins rode straight through PR review (found in the
2026-08-04 PRR: cryptography CVE-2026-69247, aiohttp CVE-2026-69244).

pip-audit is now enforcing, but its database can lag a freshly published
advisory. These floors pin the *adjudicated* minimums so a merge that
resolves a requirements conflict by reverting to an older pin — exactly how
the Redis merge reintroduced two other defects — fails loudly with the CVE
it would be reintroducing.

When a new advisory raises a floor: bump the pin, then record the new floor
here with its advisory ID.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# package -> (minimum safe version, advisory that set the floor)
#
# click is deliberately NOT here: PYSEC-2026-2132 wants click>=8.3.3, but
# gtts (latest 2.5.4) hard-caps click<8.2, so the lockfile cannot carry the
# fixed version. That advisory is instead an adjudicated `--ignore-vuln` in
# .github/workflows/dependency-audit.yml with the rationale inline.
FLOORS = {
    "aiohttp": ((3, 14, 3), "CVE-2026-69244 (HIGH, OOB heap read) + CVE-2026-69243 (request smuggling)"),
    "cryptography": ((50, 0, 0), "CVE-2026-69247 (HIGH, Bleichenbacher oracle in PKCS#7 decrypt)"),
    "pytest": ((9, 0, 3), "PYSEC-2026-1845"),
}

REQUIREMENT_FILES = ["requirements.txt", "requirements-ml.txt"]

_PIN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)(?:\[[^\]]*\])?==(?P<ver>[0-9][0-9a-zA-Z.]*)")


def _parse_version(ver: str) -> tuple:
    parts = []
    for piece in ver.split("."):
        digits = re.match(r"\d+", piece)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts)


def _pins(path: Path) -> dict:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line.strip())
        if m:
            pins[m.group("name").lower().replace("_", "-")] = m.group("ver")
    return pins


def test_no_requirement_file_pins_below_a_security_floor():
    violations = []
    for fname in REQUIREMENT_FILES:
        path = REPO / fname
        if not path.exists():
            continue
        pins = _pins(path)
        for pkg, (floor, advisory) in FLOORS.items():
            ver = pins.get(pkg)
            if ver is not None and _parse_version(ver) < floor:
                violations.append(
                    f"{fname}: {pkg}=={ver} is below the security floor "
                    f"{'.'.join(map(str, floor))} — reintroduces {advisory}"
                )
    assert not violations, "\n".join(violations)


def test_floors_cover_the_packages_they_claim():
    # The floor table is only useful if the packages are actually pinned
    # somewhere — a typo'd name here would silently guard nothing.
    all_pins = {}
    for fname in REQUIREMENT_FILES:
        path = REPO / fname
        if path.exists():
            all_pins.update(_pins(path))
    missing = [pkg for pkg in FLOORS if pkg not in all_pins]
    assert not missing, f"floor entries match no pin in any requirements file: {missing}"

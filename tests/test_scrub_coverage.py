"""scripts/scrub_coverage.py: counts only, and only an uncovered match fails.

Synthetic rules and text: nothing here is a real name.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("scrub_coverage", ROOT / "scripts" / "scrub_coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RULES = "\n".join([
    r"\bZorblat Towers\b => the project",
    "# a comment",
    r"\bQUX\d+ => the project",
    "Wibble Holdings",
])


def test_rules_parse_in_the_secrets_own_format():
    cov = _load()
    assert cov.parse_rules(RULES) == [r"\bZorblat Towers\b", r"\bQUX\d+", "Wibble Holdings"]


def _scrub_only(*phrases):
    def scrub(text):
        for p in phrases:
            text = text.replace(p, "the project")
        return text
    return scrub


def test_full_coverage_passes_with_counts_only():
    cov = _load()
    corpus = ["Zorblat Towers method statement", "QUX12 drawing", "nothing here"]
    result = cov.coverage(cov.parse_rules(RULES), corpus, _scrub_only("Zorblat Towers", "QUX12"))
    assert result["uncovered_matches"] == 0
    assert result["rules_matching_corpus"] == 2 and result["matches"] == 2
    assert "Zorblat" not in json.dumps(result) and "QUX" not in json.dumps(result)


def test_a_match_the_structural_rules_leave_standing_fails_by_index():
    cov = _load()
    corpus = ["Zorblat Towers method statement", "QUX12 drawing"]
    result = cov.coverage(cov.parse_rules(RULES), corpus, _scrub_only("Zorblat Towers"))
    assert result["uncovered_matches"] == 1
    assert result["uncovered_rule_indexes"] == [1]


def test_an_unparseable_rule_is_counted_not_printed():
    cov = _load()
    result = cov.coverage(["(unclosed"], ["text"], lambda t: t)
    assert result["unparseable_rules"] == 1


def test_no_credentials_fails_closed_with_a_reason(tmp_path, monkeypatch):
    cov = _load()

    def refuse(*argv):
        raise cov.CoverageError("aws ecs describe-services failed: no credentials")

    monkeypatch.setattr(cov, "_aws", refuse)
    out = tmp_path / "c.json"
    assert cov.main(["--out", str(out)]) == 1
    assert json.loads(out.read_text()) == {"ok": False, "error": "aws ecs describe-services failed: no credentials"}


def test_a_comparison_that_read_nothing_is_not_a_pass():
    cov = _load()
    result = cov.coverage(cov.parse_rules(RULES), [], lambda t: t)
    result["structural_rules"] = 0
    assert cov.vacuous(result)
    result = cov.coverage(cov.parse_rules(RULES), ["unrelated text"], lambda t: t)
    result["structural_rules"] = 5
    assert cov.vacuous(result) == "no secret rule matched the text read"
    result = cov.coverage(cov.parse_rules(RULES), ["Zorblat Towers"], _scrub_only("Zorblat Towers"))
    result["structural_rules"] = 5
    assert cov.vacuous(result) == ""

#!/usr/bin/env python3
"""Do the structural scrub rules cover everything the retired secret covered?

Run ONCE, on Actions (.github/workflows/scrub-coverage.yml), before the
RAG_SCRUB_RULES secret is retired. It reads the secret's rules and the live
projects store through the deploy credentials, finds every string a secret
rule matches in the text the scrub protects -- the scrubbed projects' own
records, their document filenames and a bounded sample of their indexed text
-- and checks that the STRUCTURAL scrub (app/core/identifier_scrub.py)
removes each one in place.

Prints COUNTS ONLY: how many secret rules, how many matched the corpus, how
many matched strings, how many the structural rules leave standing, and the
index of each rule with an uncovered match. Never a rule, a match, a name or a
value. Exit 0 only when nothing is left standing.

Read-only against the live database; changes nothing anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
SECRET_NAME = "RAG_SCRUB_RULES"
DB_NAME = "DATABASE_URL"
#: Characters of text kept either side of a match when it is scrubbed in place.
CONTEXT = 80


class CoverageError(RuntimeError):
    """The check cannot give an answer; the message says why (never a value)."""


def parse_rules(raw: str) -> List[str]:
    """The secret's rule PATTERNS, in its own format (one per line,
    ``<regex> => <replacement>``; ``\\n`` escapes allowed)."""
    patterns: List[str] = []
    for line in (raw or "").replace("\\n", "\n").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line.split("=>", 1)[0].strip() if "=>" in line else line)
    return patterns


def coverage(patterns: Sequence[str], corpus: Iterable[str], scrub) -> Dict[str, object]:
    """Counts only. ``scrub`` is the structural scrub (text -> text)."""
    compiled: List[Optional[re.Pattern]] = []
    bad = 0
    for p in patterns:
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error:
            compiled.append(None)
            bad += 1
    texts = [t for t in corpus if t]
    matched_rules = set()
    uncovered_rules = set()
    matches = 0
    uncovered = 0
    for index, rule in enumerate(compiled):
        if rule is None:
            continue
        for text in texts:
            for m in rule.finditer(text):
                matched_rules.add(index)
                matches += 1
                window = text[max(0, m.start() - CONTEXT): m.end() + CONTEXT]
                if rule.search(scrub(window)):
                    uncovered += 1
                    uncovered_rules.add(index)
    return {
        "secret_rules": len(patterns),
        "unparseable_rules": bad,
        "rules_matching_corpus": len(matched_rules),
        "matches": matches,
        "uncovered_matches": uncovered,
        "uncovered_rule_indexes": sorted(uncovered_rules),
        "corpus_texts": len(texts),
    }


def vacuous(result: Dict[str, object]) -> str:
    """Why a comparison proved nothing, or "". A check that read no text, built
    no structural rule or matched no secret rule cannot say the secret is
    covered -- it fails, never passes."""
    if not result.get("corpus_texts"):
        return "no text of the scrubbed projects was read"
    if not result.get("structural_rules"):
        return "the structural scrub built no rule"
    if not result.get("rules_matching_corpus"):
        return "no secret rule matched the text read"
    return ""


# -- live reads (deploy credentials) --------------------------------------------


def _aws(*argv: str) -> object:
    proc = subprocess.run(["aws", *argv, "--output", "json"], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        # stderr names the API and the refusal, never a secret value.
        raise CoverageError(f"aws {argv[0]} {argv[1]} failed: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "null")


def _resolve(value_from: str) -> str:
    if ":ssm:" in value_from:
        name = value_from.split(":parameter", 1)[-1]
        got = _aws("ssm", "get-parameter", "--name", name, "--with-decryption")
        return str(got["Parameter"]["Value"])
    # Secrets Manager: arn[:json-key:version-stage:version-id]
    parts = value_from.split(":")
    arn = ":".join(parts[:7])
    json_key = parts[7] if len(parts) > 7 else ""
    got = _aws("secretsmanager", "get-secret-value", "--secret-id", arn)
    secret = str(got.get("SecretString") or "")
    if json_key:
        secret = str(json.loads(secret)[json_key])
    return secret


def live_values(cluster: str, service: str, names: Sequence[str]) -> Dict[str, str]:
    """The named values (plain or secret), and EVERY plain ``environment``
    value of the live task into os.environ: the scrub's own configuration
    (which project is the master-corpus source, which are general knowledge)
    is task config, and without it the check reads the defaults and compares
    nothing."""
    svc = _aws("ecs", "describe-services", "--cluster", cluster, "--services", service)
    td_arn = svc["services"][0]["taskDefinition"]
    td = _aws("ecs", "describe-task-definition", "--task-definition", td_arn)["taskDefinition"]
    out: Dict[str, str] = {}
    for container in td.get("containerDefinitions") or []:
        for row in container.get("environment") or []:
            if row.get("name") in names:
                out[row["name"]] = str(row.get("value") or "")
            elif row.get("name"):
                os.environ[str(row["name"])] = str(row.get("value") or "")
        for row in container.get("secrets") or []:
            if row.get("name") in names and row["name"] not in out:
                out[row["name"]] = _resolve(str(row.get("valueFrom") or ""))
    return out


def live_corpus(sample_chunks: int, counts: Optional[Dict[str, object]] = None) -> Tuple[List[str], int]:
    """(texts, scrubbed project count): the scrubbed projects' records, their
    document filenames, and up to ``sample_chunks`` indexed chunks each."""
    from sqlalchemy import text as sql

    from app.core import identifier_scrub, projects

    spec = identifier_scrub._load_spec()
    ids = identifier_scrub._scrubbed_project_ids(spec)
    texts: List[str] = []
    counts = counts if counts is not None else {}
    projects._ensure_db()
    with projects.SessionLocal() as session:
        counts["db_dialect"] = session.get_bind().dialect.name
        counts["project_rows"] = counts["document_rows"] = counts["chunk_rows"] = 0
        for pid in ids:
            for row in session.execute(sql(
                "SELECT name, client, location FROM projects WHERE id = :pid"
            ), {"pid": pid}):
                counts["project_rows"] += 1
                texts.extend(str(v) for v in row if v)
            for (name,) in session.execute(sql(
                "SELECT original_name FROM documents WHERE project_id = :pid"
            ), {"pid": pid}):
                counts["document_rows"] += 1
                if name:
                    texts.append(str(name))
                    texts.append(str(name).replace("_", " "))
            for (body,) in session.execute(sql(
                "SELECT text FROM chunks WHERE project_id = :pid ORDER BY doc_id, chunk_index LIMIT :n"
            ), {"pid": pid, "n": int(sample_chunks)}):
                counts["chunk_rows"] += 1
                if body:
                    texts.append(str(body))
    return texts, len(ids)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cluster", default=os.environ.get("ECS_CLUSTER", "cerebrum"))
    ap.add_argument("--service", default="the-fork")
    ap.add_argument("--sample-chunks", type=int, default=3000)
    ap.add_argument("--out", default="scrub-coverage.json")
    args = ap.parse_args(argv)
    try:
        values = live_values(args.cluster, args.service, [SECRET_NAME, DB_NAME])
        if not values.get(SECRET_NAME):
            raise CoverageError(f"the live task definition carries no {SECRET_NAME}: nothing to compare")
        if not values.get(DB_NAME):
            raise CoverageError(f"the live task definition carries no {DB_NAME}: the projects store cannot be read")
        patterns = parse_rules(values.pop(SECRET_NAME))
        os.environ[DB_NAME] = values.pop(DB_NAME)
        os.environ.pop(SECRET_NAME, None)  # the structural scrub must not see it
        sys.path.insert(0, str(ROOT))
        counts: Dict[str, object] = {}
        texts, n_projects = live_corpus(args.sample_chunks, counts)
        from app.core import identifier_scrub

        identifier_scrub._reset_cache()
        result = coverage(patterns, texts, identifier_scrub.scrub_identifiers)
        result.update(counts)
        result["scrubbed_projects"] = n_projects
        result["structural_rules"] = identifier_scrub.rules_loaded()
        result["vacuous"] = vacuous(result)
        result["ok"] = (
            not result["vacuous"]
            and result["uncovered_matches"] == 0
            and result["unparseable_rules"] == 0
        )
    except CoverageError as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 -- a driver message can carry a connection string
        result = {"ok": False, "error": f"{type(exc).__name__} while reading the live store (message withheld)"}
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = ["### Scrub coverage (counts only)", ""] + [f"- {k}: {v}" for k, v in result.items()]
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

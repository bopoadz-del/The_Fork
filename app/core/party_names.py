"""No party names leave this RAG.

Owner ruling, 2026-09-19: "No names at all from this RAG. No employer, project
name, no consultant, no contractor, no engineer."

``app/core/identifier_scrub.py`` removes names from a denylist kept in the
environment: whoever runs the deployment has to know every name in advance and
type it in. That is the right tool for a project's NAME, which no document
labels. It is the wrong tool for the PARTIES, because the documents label
them: a contract says who the Employer, the Engineer and the Contractor are,
in so many words. This module reads those labels out of the very excerpts the
answer was written from and replaces the names with the roles -- so the next
contract is covered the day it is uploaded, with nobody maintaining a list.

Facts stay. "The Engineer must respond within 28 days" is the product;
"<firm> must respond within 28 days" is the leak.

``RAG_WITHHOLD_PARTY_NAMES=0`` turns it off, for a deployment whose corpus
belongs to the people asking (a private per-client layer).
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

_ROLES: Dict[str, str] = {
    "employer": "the Employer",
    "client": "the Employer",
    "engineer": "the Engineer",
    "contractor": "the Contractor",
    "consultant": "the Consultant",
}
_ROLE_ALT = "|".join(_ROLES)

# What a legal person's name ends in. Used to decide a run of capitalised
# words IS a name, and never scrubbed on its own.
_FIRM_SUFFIX_RE = re.compile(
    r"(?i)\b(?:limited|ltd\.?|llc|l\.l\.c\.?|llp|plc|inc\.?|gmbh|s\.a\.?|co\.?|"
    r"company|corporation|corp\.?|group|partners|consultants|consultancy|"
    r"contracting|engineering|associates|holdings?|est\.?|establishment)\b"
)
_GENERIC_WORDS = frozenset("""
the a an of and for in on at to by with as or not no any all under per
company limited ltd llc llp plc inc co corporation corp group partners
consultants consultancy contracting engineering associates holding holdings
establishment investment investments international national general saudi
arabia arabian gulf middle east project projects works contract contracts
employer client engineer contractor consultant representative party parties
details address addresses communications clause data description
""".split())

# "Engineer EXAMPLECO(EX2M Arabia Limited)", "Employer | Name Ltd", "Engineer: Name"
_ROLE_THEN_NAME_RE = re.compile(
    rf"(?im)(?:^|[\s|:(])(?P<role>{_ROLE_ALT})\b(?!['’]s)\s*[:|–\-]?\s*[|]?\s*"
    r"(?P<name>[A-Z][^\n|]{2,90})"
)
# "between X as the Employer and Y as the Contractor"
_NAME_AS_THE_ROLE_RE = re.compile(
    rf"(?i)(?:between|and)\s+(?P<name>[A-Z][^\n|]{{2,90}}?)\s+(?:\(.{{0,40}}?\)\s+)?"
    rf"as\s+the\s+(?P<role>{_ROLE_ALT})\b"
)
# Where a name ends: the next role word, a number (an address), a cell edge,
# or two lower-case words in a row -- running prose. That last test is
# CASE-SENSITIVE on purpose: matched case-insensitively it read "Gate Company"
# as prose and cut "Exampleton Gate Company Limited" down to one word.
_NAME_STOP_RE = re.compile(
    rf"(?i:\s(?:{_ROLE_ALT})\b)|\s\d|\s[a-z]{{4,}}\s[a-z]{{3,}}\s|[:;|]"
    r"|(?i:\s(?:volume|clause|schedule|p\.?o\.?\s*box)\b)"
)


_WHO_IS_A_PARTY_RE = re.compile(
    rf"(?i)\b(?:who\s+(?:is|are|was)|name\s+(?:of\s+)?the|"
    rf"which\s+(?:firm|company|entity|organi[sz]ation)\s+(?:is|was|acts?\s+as))\b"
    rf"[^?.]{{0,40}}\b(?:{_ROLE_ALT})\b(?!['’]s\s+representative)"
)

WITHHELD_INSTRUCTION = (
    "PARTY NAMES WITHHELD — names of the parties (the Employer, the Engineer, "
    "the Contractor, any Consultant) and of the project are not disclosed on "
    "this platform. Do NOT state the name of any company or person, even "
    "though an excerpt below gives it. Answer with the ROLE and where it is "
    "recorded — for example: \"The Engineer is named in the Contract Data "
    "(give the clause); party names are withheld on this platform.\" Every "
    "other fact in the excerpts may be stated as usual.\n"
)


def query_asks_who_a_party_is(query: str) -> bool:
    """True for "Who is the Engineer / Employer / Contractor / Consultant?"."""
    return bool(_WHO_IS_A_PARTY_RE.search(query or ""))


def withheld_answer_line(query: str) -> str:
    """The deterministic no-name answer for a who-is-a-party question."""
    m = re.search(rf"(?i)\b({_ROLE_ALT})\b", query or "")
    role = _ROLES[m.group(1).lower()] if m else "the party"
    return (
        f"{role[0].upper()}{role[1:]} is named in the Contract Data; party names "
        "are withheld on this platform."
    )


def party_names_withheld() -> bool:
    """ON unless explicitly switched off. An unrecognised value keeps it ON."""
    raw = (os.getenv("RAG_WITHHOLD_PARTY_NAMES", "") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _trim(candidate: str) -> str:
    stop = _NAME_STOP_RE.search(candidate)
    name = candidate[: stop.start()] if stop else candidate
    return re.sub(r"\s+", " ", name).strip(" \t.,:;|-–*_")


def _is_a_name(name: str) -> bool:
    if len(name) < 4 or name.lower() in _GENERIC_WORDS:
        return False
    if _FIRM_SUFFIX_RE.search(name):
        return True
    return bool(re.search(r"\b[A-Z][A-Z0-9&]{2,}\b", name))  # an acronym-style firm


def extract_party_names(excerpts: str) -> List[Tuple[str, str]]:
    """``(name, role)`` for every party the excerpts themselves label."""
    found: Dict[str, str] = {}
    text = excerpts or ""
    for rx in (_NAME_AS_THE_ROLE_RE, _ROLE_THEN_NAME_RE):
        for m in rx.finditer(text):
            name = _trim(m.group("name"))
            if _is_a_name(name):
                found.setdefault(name, _ROLES[m.group("role").lower()])
    return list(found.items())


def _variants(name: str) -> List[str]:
    """The registered name and the shorter things people actually write.

    ``EXAMPLECO(EX2M Arabia Limited)`` is written "EXAMPLECO", "EX2M", "EX2M
    Arabia Limited"; ``Exampleton Gate Company Limited`` is written
    "Exampleton Gate". A variant is only used when it still identifies
    someone: a distinctive word, never "Company Limited" on its own.
    """
    out = {name}
    outer = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
    inner = " ".join(re.findall(r"\(([^)]{2,60})\)", name))
    for part in (outer, inner):
        if part:
            out.add(part)
            words = part.split()
            while words and words[-1].lower().strip(".,") in _GENERIC_WORDS:
                words.pop()
            if words:
                out.add(" ".join(words))
            for w in words:
                bare = w.strip(".,()&")
                if len(bare) >= 4 and bare.lower() not in _GENERIC_WORDS and (
                    bare.isupper() or not bare.islower()
                ) and re.search(r"[A-Za-z]", bare):
                    out.add(bare)
    # Longest first, so "Exampleton Gate Company Limited" goes before
    # "Exampleton Gate" and leaves no orphaned "Company Limited" behind.
    return sorted((v for v in out if len(v) >= 4), key=len, reverse=True)


def _acronym(name: str) -> str:
    """Initials of the registered name: how correspondence refers to a party.

    Letters and minutes write "BTHC shall ..." for "Bexample Trading & Haulage
    Company". Matched CASE-SENSITIVELY and only at three letters or more, so
    it can never touch an ordinary word.
    """
    outer = re.sub(r"\s*\(.*?\)\s*", " ", name)
    initials = [
        w[0] for w in re.findall(r"[A-Za-z][A-Za-z'-]*", outer)
        if w.lower() not in ("and", "of", "the", "for")
    ]
    acronym = "".join(initials).upper()
    return acronym if len(acronym) >= 3 else ""


def withhold_party_names(answer: str, excerpts: str) -> str:
    """``answer`` with every party the excerpts name replaced by its role."""
    if not answer or not excerpts or not party_names_withheld():
        return answer
    out = answer
    for name, role in extract_party_names(excerpts):
        for variant in _variants(name):
            pattern = re.compile(
                r"(?<![A-Za-z0-9])" + re.escape(variant).replace(r"\ ", r"\s+")
                + r"(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
            out = pattern.sub(role, out)
        acronym = _acronym(name)
        if acronym:
            out = re.sub(
                r"(?<![A-Za-z0-9])" + re.escape(acronym) + r"(?![A-Za-z0-9])", role, out,
            )
    # "the Engineer (the Engineer)" / "**the Engineer the Engineer**" after a
    # name and its bracketed alias both collapse to the same role.
    for role in set(_ROLES.values()):
        r = re.escape(role)
        out = re.sub(rf"{r}\s*[\(\[]\s*{r}\s*[\)\]]", role, out, flags=re.IGNORECASE)
        out = re.sub(rf"{r}(?:\s+{r})+", role, out, flags=re.IGNORECASE)
    return out

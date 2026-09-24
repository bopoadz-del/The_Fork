# BRANCH_NOTES — `agent-e/s1-s2-s4-first-line-hard-rule`

Local lane for Agent D to fold. No pull request, no merge, no deploy.

Base: `209bc8363df8168f9feacc88cebc28a0752b9942` (tip `209bc83`).

## Corpus truth

S2 compaction is **PRESENT**. The numeric figure is in Vol 5 Other Documents
(geotech RSM), not in the usual Vol 2 Specification sentence: sub-grade
compacted to **95% of maximum dry density** for a minimum CBR of 25, tied to
pavement / subgrade design. Vol 2 often only says "properly compacted". That
qualitative sentence is what live SET5 S2 first lines quoted. Do not rewrite
S2 as absent, and do not treat a formatting change as the fix.

S1 minimum cover (75 mm) and S4 governing contract(s) are present as well
(close-out SPLITs).

## Tests added

`tests/test_s1_s2_s4_first_line_hard_rule.py` — synthetic filenames and
quotes only. No client documents.

They failed on the unchanged post-check (13 failures): the Hard-rule text
did not yet require the numeric figure and the document together, and
`_postprocess_answer` left these first lines in place:

- S1: `75 mm` with no document, or the document with no `75 mm`, or a
  narrative opener.
- S2: specification name + "properly compacted", with `95%` sitting in the
  injected Vol-5-style excerpt.
- S4: a governing-law opener that never names the visible contract; two
  contracts were answered by naming one.

A first line that only says "properly compacted" plus the specification name
fails the S2 test. The same test passes once the first line carries `95%`
and the document that states it.

## What changed

1. Hard-rule wording in `app/agents/configs/project-assistant.md` and
   `app/agents/configs/heavy-reasoning.md` (same two bullets in each).
2. Deterministic guard `app/agents/first_line_hard_rule.py`, called at the
   end of `_postprocess_answer` in `app/agents/runtime.py`.
   Kill-switch: `FIRST_LINE_HARD_RULE=0`.

The guard reads the excerpts already on the turn. It does not invent a
percent: excerpts that only say "properly compacted" are left alone. When
the question names a source class and that class's own document states a
percent, that percent is the first-line figure (specification 98% stays
ahead of another file's 95%). When the number is in a different document,
the first line names that file and says it is not the specification.
"Which contract governs this project?" names the one non-template contract,
or asks which contract is meant when more than one is visible. A template
filename is not a second contract.

## Hard-rule text, before → after

Governing-source bullet, before:

> Open with the figure and the document that carries it -- "Specification Section 03 30 00 gives 75 mm" -- not with a paragraph that reaches the citation at the end.

Governing-source bullet, after (the new obligation):

> The FIRST line must contain BOTH the numeric figure (with its unit or percent) AND the document name that carries it -- "Specification Section 03 30 00 gives 75 mm" -- not a paragraph that reaches the citation at the end, and not a figure whose document appears only in a later sentence. A qualitative clause ("properly compacted", "as required") is not the figure when the retrieved context also states a number (for example 95% of maximum dry density). Lead with that number and the filename of the document that states it, in the FIRST line, even when that document is a different volume from the one the question named.

Contract bullet, before:

> When the question says only "the contract", "the project" or "the specification", name the one you used -- its number and title -- in the FIRST line of the answer, and cite it. When the choice would change the answer and nothing in the question or the conversation picks one, ask which is meant instead of choosing silently.

Contract bullet, after (sentence added):

> "Which contract governs this project?" is the same duty: the FIRST line names that contract's number and title when one contract is visible in the retrieved context, and when the retrieved context names more than one contract the FIRST line asks which is meant.

## How D should fold

Fold this branch into the one batch PR. Do not open a second PR and do not
deploy from this branch.

Retrieval follow-up, out of scope here: live S2 still tends to surface Vol 2
Specification ("properly compacted") and not the Vol 5 geotech passage that
holds 95% MDD. This guard will not write 95% unless that passage is in the
turn's excerpts. If a later close-out is still S2 0/6, the next change is
retrieval of the numeric compaction passage, not another prompt edit.

## Local proof

- `CEREBRUM_VIRGIN=false` + `CEREBRUM_DOMAIN_KITS=construction`: the new
  file, name-the-contract, named-source ranking, coverage honesty, and
  citation provenance — 100 passed.
- `CEREBRUM_VIRGIN=true`: the new file, name-the-contract, named-source
  ranking, and coverage honesty — 56 passed; citation provenance — 44 passed.
- `scripts/scan_exception_pass.py`: no silent `except Exception: pass`;
  `RETURN: 0 new` (76 baselined).

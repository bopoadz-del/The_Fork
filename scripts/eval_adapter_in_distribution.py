#!/usr/bin/env python3
"""In-distribution adapter eval — 10 questions whose answers are derivable
verbatim from the training corpus, with per-question substring scoring.

This eval pairs with ``eval_adapter_head_to_head.py`` (the out-of-
distribution smoke set) to disentangle two failure modes:

* If OOD does badly but IN does well, the adapter learned the domain
  but evals are testing topics outside the training set.
* If IN also does badly, the adapter is overfitting on surface format
  rather than internalising the domain.

Each query has an ``expected`` list — substrings that MUST appear in the
adapter response (case-insensitive) for the row to be marked pass.

Sources of ground truth (per question, in the test data):
* score_risk / next_ncr_status / generate_doc_number / validate_design_status
  -- derived live from ``app.core.construction_knowledge`` in the test data
  generator, so they are guaranteed exact.
* evm.md Section 7 + Section 20 -- literal source text.
* construction_expert.txt PRC-501 + CRITICAL_RULES.no_approved_on_design --
  literal source text.
* Diriyah CESMM4 description-first anchors -- the rows hand-authored from
  the verified BOQ page (D999.46 = 10,317.00 SAR/m; D999.14 = classification
  reference, no own rate).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class InQuery:
    query: str
    expected: List[str]   # substrings that must ALL appear (case-insensitive)
    forbidden: List[str]  # substrings that must NOT appear (catches the
                          # round-3 failure mode where adapter invents a
                          # fake clause name etc.)
    source: str
    notes: str = ""


_QUERIES: List[InQuery] = [
    # --- knowledge_functions: run-the-code answers ----------------------------
    InQuery(
        query="Score the risk for probability 4 and impact 5 per PRC-302.",
        expected=["20", "RED"],
        forbidden=[],
        source="knowledge_functions/score_risk",
        notes="score_risk(4,5) -> score=20, band=RED, requires_action=True",
    ),
    InQuery(
        query="What document number does generate_doc_number produce for NCR sequence 7 in year 2026?",
        expected=["NCR-2026-007"],
        forbidden=[],
        source="knowledge_functions/generate_doc_number",
        notes="generate_doc_number('NCR', 7, year=2026) -> 'NCR-2026-007'",
    ),
    InQuery(
        query="In the PRC-402 NCR workflow, what status follows 'DISPOSITION_REVIEWED'?",
        expected=["APPROVED"],
        forbidden=[],
        source="knowledge_functions/next_ncr_status",
        notes="next_ncr_status('DISPOSITION_REVIEWED') -> 'APPROVED'",
    ),
    InQuery(
        query="Is 'APPROVED' a valid design review status per PRC-501?",
        expected=["forbidden", "PRC-501"],
        forbidden=["yes — 'APPROVED' is a valid"],
        source="knowledge_functions/validate_design_status",
        notes="validate_design_status('APPROVED') -> (False, '... forbidden ... PRC-501 ...')",
    ),

    # --- evm_knowledge: literal source text -----------------------------------
    InQuery(
        query="What CPI threshold triggers RED status in the traffic light system?",
        expected=["0.90", "RED"],
        forbidden=[],
        source="evm_knowledge/section_7",
        notes="RED if CPI < 0.90 (verbatim from construction_evm.md)",
    ),
    InQuery(
        query="In the Section 20 worked overrun example, what is the computed TCPI?",
        expected=["1.19"],
        forbidden=[],
        source="evm_knowledge/section_20",
        notes="TCPI = (50,000,000 - 28,500,000) / (50,000,000 - 32,000,000) = 21.5M / 18M = 1.19",
    ),

    # --- procedure_knowledge: literal source text -----------------------------
    InQuery(
        query="What is the minimum review distribution period before a Design Review Workshop per PRC-501?",
        expected=["7", "calendar"],
        forbidden=[],
        source="procedure_knowledge/PRC-501",
        notes="Minimum review distribution period: 7 calendar days (verbatim)",
    ),
    InQuery(
        query="What is the contract rule about using 'APPROVED' on design documents?",
        expected=["APPROVED", "design"],
        forbidden=[],
        source="procedure_knowledge/CRITICAL_RULES.no_approved_on_design",
        notes="Rule: Never use 'APPROVED' on design documents. The word 'APPROVED' is contractually prohibited.",
    ),

    # --- boq_docs: description-first CESMM4 anchors ---------------------------
    InQuery(
        query="What is the unit rate for protection of an existing 600mm wastewater pipeline at average depth 5-6m in the Diriyah Gate Phase II BOQ?",
        expected=["10,317", "SAR/m"],
        forbidden=[],
        source="boq_docs/D999.46_anchor",
        notes="10,317.00 SAR/m for 600mm waste water pipe, 5-6m depth (CESMM4 ref D999.46)",
    ),
    InQuery(
        query="Does the CESMM4 reference D999.14 itself carry a unit rate, or do rates attach to the described items under that classification?",
        expected=["classification", "not"],
        forbidden=["1,060", "1060", "12.00", "SAR/m for D999.14", "is 10,317"],
        source="boq_docs/D999.14_corrective",
        notes="D999.14 is a CESMM4 classification reference; rates attach to described items, not to the code itself. Adapter must NOT fabricate a rate for D999.14.",
    ),
]


@dataclass
class Sample:
    text: str
    stop_reason: str


def _require_api_key() -> str:
    key = os.environ.get("TINKER_API_KEY")
    if not key:
        raise RuntimeError("TINKER_API_KEY not set.")
    return key


def _format_chat_prompt(tokenizer, user_text: str):
    from tinker.types import ModelInput
    user_msg = {"role": "user", "content": user_text}
    ids: List[int] = tokenizer.encode_message_with_chat_template(user_msg, [user_msg])
    return ModelInput.from_ints(ids), ids


def _decode_response(tokenizer, prompt_ids: List[int], sequence) -> Sample:
    full_tokens = list(sequence.tokens_np) if hasattr(sequence, "tokens_np") else list(sequence._tokens_list)
    if full_tokens[: len(prompt_ids)] == prompt_ids:
        gen_tokens = full_tokens[len(prompt_ids):]
    else:
        gen_tokens = full_tokens
    text = tokenizer.decode(gen_tokens, skip_special_tokens=True)
    stop = str(getattr(sequence, "stop_reason", "?"))
    return Sample(text=text.strip(), stop_reason=stop)


def _sample(client, tokenizer, user_text: str, max_new_tokens: int, temperature: float) -> Sample:
    import tinker
    prompt, prompt_ids = _format_chat_prompt(tokenizer, user_text)
    params = tinker.SamplingParams(max_tokens=max_new_tokens, temperature=temperature, top_p=0.9)
    fut = client.sample(prompt=prompt, num_samples=1, sampling_params=params)
    resp = fut.result()
    seqs = resp.sequences
    if not seqs:
        return Sample(text="(no sequence returned)", stop_reason="empty")
    return _decode_response(tokenizer, prompt_ids, seqs[0])


def _score(text: str, query: InQuery) -> dict:
    """Substring-match scoring. PASS iff every expected is present AND no
    forbidden phrase appears."""
    tl = text.lower()
    found_expected = [e for e in query.expected if e.lower() in tl]
    missing_expected = [e for e in query.expected if e.lower() not in tl]
    found_forbidden = [f for f in query.forbidden if f.lower() in tl]
    passed = not missing_expected and not found_forbidden
    return {
        "passed": passed,
        "found_expected": found_expected,
        "missing_expected": missing_expected,
        "forbidden_hits": found_forbidden,
    }


def _ensure_sampler_path(service, training_path: str) -> str:
    if "/sampler_weights/" in training_path:
        return training_path
    logger.info("converting training-weights checkpoint -> sampler-weights ...")
    training_client = service.create_training_client_from_state(path=training_path)
    sampler_name = "indist-eval-" + _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    future = training_client.save_weights_for_sampler(sampler_name)
    save_resp = future.result()
    sampler_path = getattr(save_resp, "path", None) or sampler_name
    logger.info("sampler-weights at %s", sampler_path)
    return sampler_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tinker-path", required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-new-tokens", type=int, default=300)
    parser.add_argument("--temperature", type=float, default=0.2,
                        help="Lower than the OOD eval — in-distribution should be deterministic.")
    parser.add_argument("--skip-base", action="store_true",
                        help="Only sample the adapter; skip the base run (saves cost on iteration).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    _require_api_key()
    import tinker

    service = tinker.ServiceClient()
    sampler_path = _ensure_sampler_path(service, args.tinker_path)

    logger.info("adapter client (%s)", sampler_path)
    adapter_client = service.create_sampling_client(model_path=sampler_path)
    adapter_tokenizer = adapter_client.get_tokenizer()

    base_client = None
    base_tokenizer = None
    if not args.skip_base:
        logger.info("base client (%s)", args.base_model)
        base_client = service.create_sampling_client(base_model=args.base_model)
        base_tokenizer = base_client.get_tokenizer()

    rows = []
    for i, q in enumerate(_QUERIES, start=1):
        logger.info("[%d/%d] %s", i, len(_QUERIES), q.query[:80])
        adapter_sample = _sample(adapter_client, adapter_tokenizer, q.query, args.max_new_tokens, args.temperature)
        adapter_score = _score(adapter_sample.text, q)
        base_sample = None
        base_score = None
        if base_client is not None:
            base_sample = _sample(base_client, base_tokenizer, q.query, args.max_new_tokens, args.temperature)
            base_score = _score(base_sample.text, q)
        rows.append((q, adapter_sample, adapter_score, base_sample, base_score))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    adapter_pass = sum(1 for r in rows if r[2]["passed"])
    base_pass = sum(1 for r in rows if r[4] and r[4]["passed"]) if not args.skip_base else None
    with args.out.open("w", encoding="utf-8") as f:
        f.write("# In-distribution adapter eval\n\n")
        f.write(f"- adapter (sampler-weights): `{sampler_path}`\n")
        f.write(f"- base model: `{args.base_model}`\n")
        f.write(f"- max_new_tokens={args.max_new_tokens}, temperature={args.temperature}\n")
        f.write(f"- generated: {_dt.datetime.utcnow().isoformat()}Z\n\n")
        f.write(f"## Summary\n\n")
        f.write(f"- **Adapter pass: {adapter_pass} / {len(rows)}**\n")
        if base_pass is not None:
            f.write(f"- Base pass (for reference): {base_pass} / {len(rows)}\n")
        f.write("\n## Per-question\n\n")
        f.write("| # | Source | Adapter | Base |\n|---|---|---|---|\n")
        for i, (q, a_s, a_sc, b_s, b_sc) in enumerate(rows, 1):
            a_str = "PASS" if a_sc["passed"] else "FAIL"
            b_str = "—" if b_sc is None else ("PASS" if b_sc["passed"] else "FAIL")
            f.write(f"| {i} | `{q.source}` | {a_str} | {b_str} |\n")
        f.write("\n---\n\n")
        for i, (q, a_s, a_sc, b_s, b_sc) in enumerate(rows, 1):
            f.write(f"## Q{i} — `{q.source}`\n\n")
            f.write(f"**Query:** {q.query}\n\n")
            f.write(f"**Notes (ground truth):** {q.notes}\n\n")
            f.write(f"**Expected substrings:** {q.expected}\n")
            f.write(f"**Forbidden substrings:** {q.forbidden}\n\n")
            f.write(f"### Adapter — **{'PASS' if a_sc['passed'] else 'FAIL'}**\n\n")
            f.write("```\n" + a_s.text + "\n```\n\n")
            f.write(f"- found_expected: {a_sc['found_expected']}\n")
            f.write(f"- missing_expected: {a_sc['missing_expected']}\n")
            f.write(f"- forbidden_hits: {a_sc['forbidden_hits']}\n\n")
            if b_s is not None:
                f.write(f"### Base — **{'PASS' if b_sc['passed'] else 'FAIL'}**\n\n")
                f.write("```\n" + b_s.text + "\n```\n\n")
                f.write(f"- found_expected: {b_sc['found_expected']}\n")
                f.write(f"- missing_expected: {b_sc['missing_expected']}\n")
                f.write(f"- forbidden_hits: {b_sc['forbidden_hits']}\n\n")
            f.write("---\n\n")

    logger.info("adapter pass: %d / %d -> %s", adapter_pass, len(rows), args.out)
    print(str(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

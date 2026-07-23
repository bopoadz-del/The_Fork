#!/usr/bin/env python3
"""Head-to-head smoke eval: trained adapter vs base Qwen3-4B.

For each query in :data:`_EVAL_QUERIES`, sample once from the unadapted
base and once from the LoRA-trained adapter, then write a markdown
report side-by-side with a one-line delta verdict.

The verdict heuristic is intentionally simple — this is a smoke check,
not a benchmark. It looks for grounded construction-domain markers
(specific numbers, procedure codes, formula notation, units) and
classifies each pair as ``better`` / ``worse`` / ``no change`` based
on which side carries more of those markers.

Usage:
    .venv/Scripts/python.exe scripts/eval_adapter_head_to_head.py \\
        --tinker-path 'tinker://...weights/checkpoints-...' \\
        --base-model Qwen/Qwen3-4B-Instruct-2507 \\
        --out data/learning/adapters/<run-id>/smoke_eval.md
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


_EVAL_QUERIES: List[str] = [
    "What is the unit rate for D999.14 earthworks compaction in the Diriyah BOQ?",
    "List all concrete grade specifications required for a reinforced retaining wall.",
    "A contractor claims 14 days extension of time due to late shop drawing approval. What clause applies?",
    "Calculate the EVM SPI for a project with BCWP 2,400,000 SAR and BCWS 3,000,000 SAR.",
    "What procurement lead time should I budget for structural steel sections in a Saudi project?",
]


@dataclass
class Sample:
    text: str
    stop_reason: str


def _require_api_key() -> str:
    key = os.environ.get("TINKER_API_KEY")
    if not key:
        raise RuntimeError(
            "TINKER_API_KEY not set. Export it before running this script."
        )
    return key


def _format_chat_prompt(tokenizer, user_text: str):
    """Build a chat-formatted prompt for one user turn. Returns ModelInput."""
    from tinker.types import ModelInput

    # The training run used encode_message_with_chat_template; mirror that so
    # the adapter actually sees the same prompt shape it was trained on.
    user_msg = {"role": "user", "content": user_text}
    ids: List[int] = tokenizer.encode_message_with_chat_template(user_msg, [user_msg])
    return ModelInput.from_ints(ids), ids


def _decode_response(tokenizer, prompt_ids: List[int], sequence) -> Sample:
    """Decode a SampledSequence to text, trimming the prompt prefix."""
    full_tokens = list(sequence.tokens_np) if hasattr(sequence, "tokens_np") else list(sequence._tokens_list)
    # Tinker returns the FULL (prompt + response) token sequence in
    # ``tokens_np``. Strip the prompt prefix so we only decode what the
    # model actually generated.
    if full_tokens[: len(prompt_ids)] == prompt_ids:
        gen_tokens = full_tokens[len(prompt_ids):]
    else:
        gen_tokens = full_tokens
    text = tokenizer.decode(gen_tokens, skip_special_tokens=True)
    stop = str(getattr(sequence, "stop_reason", "?"))
    return Sample(text=text.strip(), stop_reason=stop)


def _sample(
    client,
    tokenizer,
    user_text: str,
    max_new_tokens: int,
    temperature: float,
) -> Sample:
    import tinker

    prompt, prompt_ids = _format_chat_prompt(tokenizer, user_text)
    params = tinker.SamplingParams(
        max_tokens=max_new_tokens,
        temperature=temperature,
        top_p=0.9,
    )
    fut = client.sample(prompt=prompt, num_samples=1, sampling_params=params)
    resp = fut.result()
    seqs = resp.sequences
    if not seqs:
        return Sample(text="(no sequence returned)", stop_reason="empty")
    return _decode_response(tokenizer, prompt_ids, seqs[0])


# ── Verdict heuristic ─────────────────────────────────────────────────────


_GROUND_MARKERS: List[re.Pattern] = [
    re.compile(r"\bD\s*999\.\d+\b"),                 # BOQ item codes
    re.compile(r"\bPRC-\d+[A-Z]?\b"),                # procedure codes
    re.compile(r"\b(?:CPI|SPI|EAC|ETC|BCWP|BCWS|ACWP|TCPI|VAC|CV|SV)\b"),
    re.compile(r"\bSAR\b", re.IGNORECASE),
    re.compile(r"\b(?:m3|m2|m\^3|m\^2|kg|ton|tonne|MT)\b", re.IGNORECASE),
    re.compile(r"\bC\d{2,3}/\d{2,3}\b"),              # concrete grades C32/40 etc
    re.compile(r"\bC\s?\d{2,3}\b"),                   # concrete grades C30, C40
    re.compile(r"\bFOR[\s_-]COMMENT\b|\bBUY[-\s]?OFF\b|\bACCEPTANCE\b"),
    re.compile(r"\b\d{1,3}(?:,\d{3}){1,4}(?:\.\d+)?\b"),  # large numbers
    re.compile(r"\b\d+\s*(?:days?|weeks?|months?)\b", re.IGNORECASE),
    re.compile(r"=\s*0?\.\d{2,}|=\s*\d+\.\d+"),       # equality with a decimal
]


def _ground_marker_count(text: str) -> int:
    return sum(1 for p in _GROUND_MARKERS if p.search(text))


def _verdict(base: Sample, adapter: Sample, query: str) -> str:
    b = _ground_marker_count(base.text)
    a = _ground_marker_count(adapter.text)
    bl = len(base.text.strip())
    al = len(adapter.text.strip())

    if al < 20 and bl >= 20:
        return f"worse — adapter response truncated/empty ({al} chars vs {bl})"
    if bl < 20 and al >= 20:
        return f"better — adapter produced a response where base did not ({al} chars vs {bl})"

    # Q-specific check for the SPI calculation: prefer the answer that
    # contains the right ratio.
    if "SPI" in query and "BCWP" in query:
        correct = re.search(r"\b0?\.\s?8\b|\b0\.80\b", adapter.text + " ")
        if correct and not re.search(r"\b0?\.\s?8\b|\b0\.80\b", base.text):
            return "better — adapter computed SPI=0.80 correctly; base did not"

    if a > b:
        return f"better — adapter cites {a} domain markers (codes/units/numbers) vs base {b}"
    if a < b:
        return f"worse — adapter cites {a} markers vs base {b}; base hewed closer to specifics"
    return f"no change — both cite {a} markers; substantive content roughly equivalent"


# ── Main ──────────────────────────────────────────────────────────────────


def _ensure_sampler_path(service, training_path: str) -> str:
    """Tinker's sample() requires a sampler-weights checkpoint, distinct
    from the training-weights checkpoint that save_state() produces.

    If the passed path is `/weights/...`, this helper re-instantiates a
    training client from that state and calls save_weights_for_sampler
    to publish a `/sampler_weights/<name>` checkpoint. Returns the new
    path. Idempotent: if the input is already `/sampler_weights/...`,
    it's returned untouched.
    """
    if "/sampler_weights/" in training_path:
        return training_path
    logger.info("converting training-weights checkpoint → sampler-weights …")
    training_client = service.create_training_client_from_state(path=training_path)
    sampler_name = "smoke-eval-" + _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    future = training_client.save_weights_for_sampler(sampler_name)
    save_resp = future.result()
    sampler_path = getattr(save_resp, "path", None) or sampler_name
    logger.info("sampler-weights at %s", sampler_path)
    return sampler_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tinker-path", required=True,
                        help="The trained adapter's tinker:// path. If it's a "
                             "training-weights path, it's auto-converted to a "
                             "sampler-weights path via save_weights_for_sampler.")
    parser.add_argument("--base-model", default="Qwen/Qwen3-4B-Instruct-2507")
    parser.add_argument("--out", default="data/learning/adapters/smoke_eval.md",
                        type=Path)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--temperature", type=float, default=0.3)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    _require_api_key()
    import tinker

    service = tinker.ServiceClient()

    sampler_path = _ensure_sampler_path(service, args.tinker_path)

    logger.info("creating base sampling client (%s) …", args.base_model)
    base_client = service.create_sampling_client(base_model=args.base_model)
    base_tokenizer = base_client.get_tokenizer()

    logger.info("creating adapter sampling client (%s) …", sampler_path)
    adapter_client = service.create_sampling_client(model_path=sampler_path)
    adapter_tokenizer = adapter_client.get_tokenizer()

    rows: List[Tuple[str, Sample, Sample, str]] = []
    for i, q in enumerate(_EVAL_QUERIES, start=1):
        logger.info("[%d/%d] sampling: %s", i, len(_EVAL_QUERIES), q[:80])
        base_sample = _sample(
            base_client, base_tokenizer, q, args.max_new_tokens, args.temperature,
        )
        adapter_sample = _sample(
            adapter_client, adapter_tokenizer, q, args.max_new_tokens, args.temperature,
        )
        verdict = _verdict(base_sample, adapter_sample, q)
        rows.append((q, base_sample, adapter_sample, verdict))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    timestamp = _dt.datetime.utcnow().isoformat() + "Z"
    with args.out.open("w", encoding="utf-8") as f:
        f.write(f"# Head-to-head adapter smoke eval\n\n")
        f.write(f"- training-weights path: `{args.tinker_path}`\n")
        f.write(f"- sampler-weights path : `{sampler_path}`\n")
        f.write(f"- base model: `{args.base_model}`\n")
        f.write(f"- max_new_tokens: {args.max_new_tokens}, temperature: {args.temperature}\n")
        f.write(f"- generated: {timestamp}\n\n")

        # Summary table at the top.
        f.write("## Verdicts\n\n")
        f.write("| # | Query | Verdict |\n|---|---|---|\n")
        for i, (q, _, _, v) in enumerate(rows, start=1):
            short_q = q if len(q) <= 80 else q[:77] + "..."
            f.write(f"| {i} | {short_q} | {v} |\n")
        f.write("\n---\n\n")

        # Per-query detail.
        for i, (q, b, a, v) in enumerate(rows, start=1):
            f.write(f"## Q{i}\n\n")
            f.write(f"**Query:** {q}\n\n")
            f.write(f"### Base ({args.base_model})\n\n")
            f.write(f"_stop_reason: `{b.stop_reason}`_\n\n")
            f.write("```\n" + b.text + "\n```\n\n")
            f.write(f"### Adapter\n\n")
            f.write(f"_stop_reason: `{a.stop_reason}`_\n\n")
            f.write("```\n" + a.text + "\n```\n\n")
            f.write(f"**Delta:** {v}\n\n")
            f.write("---\n\n")

    logger.info("wrote eval report to %s", args.out)
    print(str(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

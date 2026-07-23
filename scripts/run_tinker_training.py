#!/usr/bin/env python3
"""End-to-end Tinker LoRA training driver (thin wrapper).

Wires together the existing scenario JSONL → Tinker SDK → adapter
checkpoint pipeline. Two execution modes:

* ``--dry-run`` (DEFAULT): contacts the Tinker control plane only
  via ``get_server_capabilities()`` — no training client is created,
  no GPU slot is reserved, no credits are spent. Loads the scenario
  JSONL, prints the planned base model, hyperparameters, sample rows,
  and the adapter output path; writes a ``metadata.json`` stub so the
  downstream loader can introspect the planned run.
* ``--execute``: instantiates ``create_lora_training_client``,
  tokenizes a single batch via the model's chat template, runs ONE
  ``forward_backward`` + ``optim_step``, saves state to a Tinker
  checkpoint path, and writes the realized ``metadata.json``. This is
  the minimum-cost path that proves the wire end-to-end on the real
  service.

Operator recipe:

    # 1. Set the env var (do NOT commit it):
    export TINKER_API_KEY=<your key>

    # 2. Dry-run — free, ~3s, no GPU touched:
    .venv/Scripts/python.exe scripts/run_tinker_training.py --dry-run

    # 3. Real 1-step smoke (cheap: a single optim step on a 1B model
    #    is fractions of a cent):
    .venv/Scripts/python.exe scripts/run_tinker_training.py --execute \\
        --base-model meta-llama/Llama-3.2-1B --max-steps 1

    # 4. Full fine-tune (multi-minute, real cost; tune --max-steps):
    .venv/Scripts/python.exe scripts/run_tinker_training.py --execute \\
        --base-model Qwen/Qwen3-4B-Instruct-2507 --max-steps 200

Cost: dry-run is free. A single 1-step LoRA pass on Llama-3.2-1B with
a ~6-row batch is well under one cent. Long runs scale roughly linearly
with ``max_steps × batch_size × token_count``; see Tinker pricing.

Inputs:
* ``data/learning/training_scenarios.jsonl`` (preferred) — produced
  by ``scripts/generate_training_scenarios.py``.
* If absent, a seed of hand-authored construction Q&A pairs is written
  to that path so the pipeline is reproducible offline.

Outputs:
* ``data/learning/adapters/{run_id}/metadata.json`` — run params.
* On ``--execute``: a Tinker-side checkpoint at the printed
  ``tinker_path``. Use ``scripts/tinker_trainer.py``'s
  ``download_checkpoint`` (or rerun with ``--resume-from``) to fetch
  the adapter archive locally.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import json
import logging
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Inline seed used when no scenarios JSONL exists yet — keeps the dry-run
# reproducible on a fresh checkout without depending on a hydrated project
# or a live LLM provider.
_SEED_SCENARIOS: List[Dict[str, str]] = [
    {
        "instruction": "What is the minimum concrete cover for slabs in moderate exposure per ACI 318?",
        "response": "ACI 318 requires a minimum cover of 30 mm (about 1 1/4 in.) for slabs cast against formwork in moderate exposure. Tighten to 40 mm in marine or de-icing-salt environments. Always check the project spec — owners frequently override upward.",
        "source": "seed.aci318",
    },
    {
        "instruction": "On a Saudi project, what is the typical lead time for imported MEP switchgear from Europe?",
        "response": "Plan for 28–36 weeks door-to-site for European-sourced LV/MV switchgear into KSA, including ex-works manufacturing, ocean freight, Jeddah/Dammam customs, and trucking to site. Order before structural slab pours start to keep MEP first-fix off the critical path.",
        "source": "seed.procurement",
    },
    {
        "instruction": "How do you size a tower crane jib for a 60m x 40m residential tower footprint?",
        "response": "Pick a jib that reaches the far corner from the crane base plus a 5 m clearance to the formwork edge. For a 60x40 m plate with the crane sited at one short edge, a 65–70 m jib covers it. Verify tip-load at the worst-case lift (typically a mivan table form package).",
        "source": "seed.logistics",
    },
    {
        "instruction": "What's the difference between a Provisional Sum and a Prime Cost Sum in a JCT contract?",
        "response": "A Provisional Sum covers work whose nature is known but quantity/spec is undefined at tender — used as a placeholder, adjusted on instruction. A Prime Cost (PC) Sum names a specific supplier or subcontractor and covers their supplied goods/services; the main contractor's overheads and profit are quoted separately against the PC. Both are reconciled at final account.",
        "source": "seed.contracts",
    },
    {
        "instruction": "When should I require a method statement vs a risk assessment from a subcontractor?",
        "response": "Require both for any non-routine activity. The risk assessment identifies hazards and residual risk after controls; the method statement is the step-by-step execution plan that operationalizes those controls. Review them together — a risk assessment without a matching method statement is a liability shield, not a safety document.",
        "source": "seed.hse",
    },
    {
        "instruction": "What's a reasonable float to carry on a 24-month commercial fit-out programme?",
        "response": "Carry 4–6 weeks of total float across the programme on a 24-month fit-out, biased toward MEP commissioning and FF&E delivery rather than structural milestones. Show it as a single end-of-programme buffer; embedded float gets eroded silently as subcontractors absorb it into their own schedules.",
        "source": "seed.planning",
    },
]


@dataclasses.dataclass
class RunPlan:
    run_id: str
    base_model: str
    lora_rank: int
    batch_size: int
    max_steps: int
    learning_rate: float
    train_data_path: str
    adapter_dir: str
    dry_run: bool
    num_train_rows: int
    timestamp: str


@dataclasses.dataclass
class RunResult:
    plan: RunPlan
    tinker_path: Optional[str]
    sample_rows: List[Dict[str, str]]


def _generate_run_id() -> str:
    return _dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def _ensure_scenarios(path: Path) -> int:
    """Ensure a scenarios JSONL exists at ``path``; seed it if missing.

    The seed is intentionally tiny — it exists so dry-runs work on a
    fresh checkout, not as a substitute for the real scenario generator.
    Returns the row count.
    """
    if path.exists() and path.stat().st_size > 0:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("no scenarios at %s — writing inline seed (%d rows)", path, len(_SEED_SCENARIOS))
    with path.open("w", encoding="utf-8") as f:
        for row in _SEED_SCENARIOS:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(_SEED_SCENARIOS)


def _load_scenarios(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("line %d malformed, skipping: %s", i, exc)
                continue
            if not obj.get("instruction") or not obj.get("response"):
                continue
            rows.append(obj)
    return rows


def _print_sample(rows: List[Dict[str, str]], n: int = 3) -> List[Dict[str, str]]:
    sample = rows[:n] if len(rows) <= n else random.Random(0).sample(rows, n)
    print("", file=sys.stderr)
    print(f"── {len(sample)} sample scenario(s) ──", file=sys.stderr)
    for i, r in enumerate(sample, start=1):
        q = (r.get("instruction") or "").strip()
        a = (r.get("response") or "").strip()
        src = r.get("source") or "?"
        print(f"  [{i}] source: {src}", file=sys.stderr)
        print(f"      Q: {q[:160]}{'…' if len(q) > 160 else ''}", file=sys.stderr)
        print(f"      A: {a[:200]}{'…' if len(a) > 200 else ''}", file=sys.stderr)
    print("", file=sys.stderr)
    return sample


def _write_metadata(plan: RunPlan, tinker_path: Optional[str]) -> Path:
    adapter_dir = Path(plan.adapter_dir)
    adapter_dir.mkdir(parents=True, exist_ok=True)
    meta_path = adapter_dir / "metadata.json"
    payload = dataclasses.asdict(plan)
    payload["tinker_path"] = tinker_path
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return meta_path


def _require_api_key() -> str:
    key = os.environ.get("TINKER_API_KEY")
    if not key:
        raise RuntimeError(
            "TINKER_API_KEY is not set. Export the key before running "
            "(see https://tinker-console.thinkingmachineslabinc.com)."
        )
    return key


def _fetch_capabilities() -> Tuple[List[str], int]:
    """Return (model_names, max_context_for_default) from the live service."""
    _require_api_key()
    import tinker

    client = tinker.ServiceClient()
    caps = client.get_server_capabilities()
    names = [m.model_name for m in caps.supported_models]
    return names, len(names)


def _build_datum_for_pair(
    tokenizer: Any,
    instruction: str,
    response: str,
    max_tokens: int,
    context: str = "",
):
    """Tokenize one (instruction, response) into a Datum suitable for
    cross-entropy training. Loss weights are 0 on prompt tokens and 1 on
    response tokens — only the assistant continuation contributes gradient.

    When ``context`` is non-empty (RAG-grounded dataset path), the user
    message is built as ``"Context:\\n<context>\\n\\nQuestion: <instruction>"``
    so the model sees retrieved evidence at training time the same way it
    will see it at inference. This is what lets the trained adapter learn
    to reason over retrieved context instead of recalling from weights.
    """
    from tinker import Datum
    from tinker.types import ModelInput

    if context:
        user_content = f"Context:\n{context}\n\nQuestion: {instruction}"
    else:
        user_content = instruction
    user_msg = {"role": "user", "content": user_content}
    assistant_msg = {"role": "assistant", "content": response}

    prompt_ids: List[int] = tokenizer.encode_message_with_chat_template(
        user_msg, [user_msg]
    )
    response_ids: List[int] = tokenizer.encode_message_with_chat_template(
        assistant_msg, [user_msg, assistant_msg]
    )

    full_ids = prompt_ids + response_ids
    if len(full_ids) > max_tokens:
        full_ids = full_ids[:max_tokens]
        if len(full_ids) <= len(prompt_ids):
            return None  # nothing left of the response after truncation

    eos = getattr(tokenizer, "eos_token_id", None) or 0
    input_ids = full_ids[:-1]
    target_ids = full_ids[1:] + [eos][: 1 if len(full_ids) == len(input_ids) else 0]
    if len(target_ids) != len(input_ids):
        target_ids = full_ids[1:]
    weights = [0.0] * len(input_ids)
    prompt_len_minus_one = max(0, len(prompt_ids) - 1)
    for i in range(prompt_len_minus_one, len(weights)):
        weights[i] = 1.0
    if sum(weights) == 0:
        return None

    return Datum(
        model_input=ModelInput.from_ints(input_ids),
        loss_fn_inputs={"target_tokens": target_ids, "weights": weights},
    )


def _execute_training(plan: RunPlan, rows: List[Dict[str, str]]) -> str:
    """Run ``plan.max_steps`` forward_backward + optim_step cycles and save
    state. Rotates the dataset, so passing fewer steps than rows/batch_size
    iterates a single random pass and more steps continues into epoch 2+.

    Returns the tinker_path of the saved checkpoint.
    """
    import tinker
    from tinker import AdamParams

    _require_api_key()
    service = tinker.ServiceClient()
    training_client = service.create_lora_training_client(
        base_model=plan.base_model,
        rank=plan.lora_rank,
    )
    tokenizer = training_client.get_tokenizer()
    info = training_client.get_info()
    max_ctx = 4096
    logger.info(
        "training client ready: model_id=%s lora_rank=%d", info.model_id, info.lora_rank
    )

    # Pre-tokenize all rows once — dropping any that don't yield a Datum
    # (over-long, no usable response, etc.).
    all_data = []
    for r in rows:
        datum = _build_datum_for_pair(
            tokenizer, r["instruction"], r["response"], max_ctx,
            context=r.get("context", ""),
        )
        if datum is not None:
            all_data.append(datum)
    if not all_data:
        raise RuntimeError("no usable training examples after tokenization")
    logger.info(
        "pre-tokenized %d/%d rows usable for training", len(all_data), len(rows)
    )

    batch_size = plan.batch_size
    n = len(all_data)
    losses: List[float] = []

    for step in range(plan.max_steps):
        # Rotating window: deterministic, ensures every row visited once
        # per `n/batch_size` steps before repeating.
        start = (step * batch_size) % n
        end = start + batch_size
        if end <= n:
            batch = all_data[start:end]
        else:
            batch = all_data[start:] + all_data[: end - n]

        fb_future = training_client.forward_backward(batch, loss_fn="cross_entropy")
        fb_result = fb_future.result()
        opt_future = training_client.optim_step(
            AdamParams(learning_rate=plan.learning_rate)
        )
        opt_future.result()

        metrics = getattr(fb_result, "metrics", None) or {}
        loss_sum = metrics.get("loss:sum") if isinstance(metrics, dict) else None
        avg = (loss_sum / len(batch)) if loss_sum is not None else None
        if avg is not None:
            losses.append(avg)
        if step % 10 == 0 or step == plan.max_steps - 1:
            logger.info(
                "step %d/%d  loss/sample=%.4f",
                step + 1,
                plan.max_steps,
                avg if avg is not None else float("nan"),
            )

    # Tinker checkpoint labels only allow [A-Za-z0-9._-]. Flatten the
    # model name AND the path separator into hyphens to satisfy the
    # validator (previously the trailing `/` between model and run_id
    # failed validation server-side).
    safe_model = plan.base_model.replace("/", "_")
    checkpoint_name = f"checkpoints-{safe_model}-{plan.run_id}"
    save_future = training_client.save_state(checkpoint_name)
    save_result = save_future.result()
    tinker_path = getattr(save_result, "path", None) or checkpoint_name
    if losses:
        logger.info(
            "training done: first_loss=%.4f  last_loss=%.4f  delta=%.4f",
            losses[0],
            losses[-1],
            losses[0] - losses[-1],
        )
    logger.info("saved state at %s", tinker_path)
    return tinker_path


# Backwards-compatible alias for code that still calls the old name.
_execute_one_step = _execute_training


def _plan_run(args: argparse.Namespace, num_rows: int) -> RunPlan:
    run_id = _generate_run_id()
    data_dir = os.getenv("DATA_DIR", "./data")
    adapter_dir = os.path.join(data_dir, "learning", "adapters", run_id)
    return RunPlan(
        run_id=run_id,
        base_model=args.base_model,
        lora_rank=args.lora_rank,
        batch_size=args.batch_size,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        train_data_path=str(args.train_data),
        adapter_dir=adapter_dir,
        dry_run=not args.execute,
        num_train_rows=num_rows,
        timestamp=_dt.datetime.utcnow().isoformat() + "Z",
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    data_dir = os.getenv("DATA_DIR", "./data")
    default_train = Path(data_dir) / "learning" / "training_scenarios.jsonl"
    parser.add_argument("--train-data", default=str(default_train), type=Path)
    parser.add_argument("--base-model", default="meta-llama/Llama-3.2-1B",
                        help="Tinker-supported base model. Cheapest defaults are "
                             "meta-llama/Llama-3.2-1B and Qwen/Qwen3-4B-Instruct-2507.")
    parser.add_argument("--lora-rank", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=1,
                        help="Number of optim steps when --execute is set.")
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="execute", action="store_false",
                      help="Default. Plan + sample only; no GPU, no spend.")
    mode.add_argument("--execute", dest="execute", action="store_true",
                      help="Run --max-steps real training steps on Tinker.")
    parser.set_defaults(execute=False)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    try:
        _require_api_key()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    try:
        model_names, _ = _fetch_capabilities()
    except Exception as exc:  # noqa: BLE001
        logger.error("could not reach Tinker control plane: %s", exc)
        return 2
    if args.base_model not in model_names:
        logger.error(
            "base model %r not in Tinker capabilities (%d models available). "
            "Pick from e.g. %s",
            args.base_model, len(model_names), ", ".join(model_names[:6]),
        )
        return 2

    num_rows = _ensure_scenarios(args.train_data)
    rows = _load_scenarios(args.train_data)
    if not rows:
        logger.error("no usable scenarios at %s", args.train_data)
        return 1

    plan = _plan_run(args, num_rows=len(rows))
    sample = _print_sample(rows, n=min(3, len(rows)))

    logger.info("── plan ──")
    for k, v in dataclasses.asdict(plan).items():
        logger.info("  %s = %s", k, v)
    logger.info("Tinker reports %d available models; chosen base is supported.", len(model_names))

    tinker_path: Optional[str] = None
    if args.execute:
        try:
            tinker_path = _execute_training(plan, rows)
        except Exception as exc:  # noqa: BLE001
            logger.exception("execute failed: %s", exc)
            _write_metadata(plan, tinker_path=None)
            return 3
    else:
        logger.info("DRY-RUN: skipping create_lora_training_client / forward_backward / optim_step.")

    meta_path = _write_metadata(plan, tinker_path=tinker_path)
    logger.info("metadata written: %s", meta_path)
    logger.info("adapter dir: %s", plan.adapter_dir)
    if tinker_path:
        logger.info("tinker_path: %s", tinker_path)

    result = RunResult(plan=plan, tinker_path=tinker_path, sample_rows=sample)
    print(json.dumps({
        "adapter_dir": result.plan.adapter_dir,
        "metadata_path": str(meta_path),
        "tinker_path": result.tinker_path,
        "dry_run": result.plan.dry_run,
        "run_id": result.plan.run_id,
        "base_model": result.plan.base_model,
        "num_train_rows": result.plan.num_train_rows,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

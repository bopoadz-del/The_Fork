#!/usr/bin/env python3
"""LoRA fine-tune of a small instruction-tuned base on construction Q&A.

Code-only scaffolding (PR 3a). Reads a JSONL of {instruction, response}
rows produced by ``scripts/export_training_set.py``, fine-tunes a LoRA
adapter on top of the configured base model, and saves to disk in
HuggingFace format. The chat block's ``use_local_model=true`` path
(see ``app/core/learning/local_model.py``) picks it up automatically.

Orin-portability invariant (binding — also enforced in local_model.py):

* No hardcoded ``.to("cuda")``. ``device`` honors GPU availability.
* Adapter saved in HF format (``adapter_model.safetensors`` +
  ``adapter_config.json``). When the Orin port runs,
  ``bitsandbytes.convert_4bit`` or a GGUF export operates on the same
  files — no format change needed. See ``docs/EDGE_PORT.md``.
* No vLLM, no Triton, no Ray Serve. Plain ``transformers.Trainer``.
* No bitsandbytes 4-bit / 8-bit. Quantization is a separate Orin step.

CLI:
    python scripts/finetune_router.py \\
        [--base-model Qwen/Qwen2.5-3B-Instruct] \\
        [--train-data data/learning/training_set.jsonl] \\
        [--output-dir data/learning/adapters/construction_v1] \\
        [--epochs 3] \\
        [--lora-r 16] \\
        [--lora-alpha 32] \\
        [--batch-size 4] \\
        [--learning-rate 2e-4] \\
        [--max-length 1024] \\
        [--val-split 0.1] \\
        [--seed 42]

NOT verified in CI: this script was written but never executed in the
authoring environment (no GPU, no Hugging Face access). The first run
on a real GPU may surface bugs that wouldn't have shipped if the
authoring run was possible. Treat the first invocation as integration
testing; report any issues against PR 3a.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from typing import List, Tuple

# Resolve the `app` package when invoked directly (not via `python -m`).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


def _device():
    """The one place in this file that says "cuda"."""
    import torch
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _format_prompt(instruction: str, response: str = "") -> str:
    """Build the instruction-tuning prompt the model trains against.

    Uses a minimal Alpaca-style template. The base models in the
    roadmap (Qwen2.5-Instruct, Llama-3.2-Instruct) ship with their
    own chat templates, but they're not consistent across families;
    a tokenizer-agnostic template keeps the training loop portable.
    """
    if response:
        return f"### Instruction:\n{instruction}\n\n### Response:\n{response}"
    return f"### Instruction:\n{instruction}\n\n### Response:\n"


def _load_jsonl(path: str) -> List[dict]:
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("line %d malformed, skipping: %s", i, exc)
    return rows


def _split_train_val(rows: List[dict], val_split: float, seed: int) -> Tuple[List[dict], List[dict]]:
    rng = random.Random(seed)
    indices = list(range(len(rows)))
    rng.shuffle(indices)
    n_val = max(1, int(len(rows) * val_split))
    val_idx = set(indices[:n_val])
    train = [rows[i] for i in range(len(rows)) if i not in val_idx]
    val = [rows[i] for i in range(len(rows)) if i in val_idx]
    return train, val


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-model",
        default=os.getenv("LOCAL_BASE_MODEL") or "Qwen/Qwen2.5-3B-Instruct",
    )
    parser.add_argument(
        "--train-data",
        default=os.path.join(
            os.getenv("DATA_DIR", "./data"), "learning", "training_set.jsonl"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(
            os.getenv("DATA_DIR", "./data"), "learning", "adapters", "construction_v1"
        ),
    )
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--val-split", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--target-modules",
        nargs="+",
        default=["q_proj", "k_proj", "v_proj", "o_proj"],
        help="Module name patterns the LoRA adapter wraps",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not os.path.exists(args.train_data):
        logger.error(
            "training data not found: %s — run scripts/export_training_set.py first",
            args.train_data,
        )
        return 1

    rows = _load_jsonl(args.train_data)
    if len(rows) < 10:
        logger.error(
            "only %d rows in %s — need at least 10 for a meaningful fine-tune",
            len(rows), args.train_data,
        )
        return 1
    train_rows, val_rows = _split_train_val(rows, args.val_split, args.seed)
    logger.info("split: %d train, %d val", len(train_rows), len(val_rows))

    # ── Heavy imports, gated until we know we have data ────────────────
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, TaskType, get_peft_model
        from transformers import (
            AutoModelForCausalLM, AutoTokenizer, DataCollatorForLanguageModeling,
            Trainer, TrainingArguments,
        )
    except ImportError as exc:
        logger.error(
            "ML deps not installed (%s). Run: pip install -r requirements-ml.txt",
            exc,
        )
        return 1

    device = _device()
    logger.info("device: %s", device)

    # ── Tokenizer + base ───────────────────────────────────────────────
    logger.info("loading tokenizer %s", args.base_model)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    logger.info("loading base model %s", args.base_model)
    base = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype="auto",  # bf16/fp16 on CUDA, fp32 on CPU
    )

    # ── LoRA adapter ───────────────────────────────────────────────────
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=args.target_modules,
        bias="none",
    )
    model = get_peft_model(base, lora_config)
    model.print_trainable_parameters()

    # ── Dataset ────────────────────────────────────────────────────────
    def tokenize(example):
        text = _format_prompt(example["instruction"], example["response"])
        # Append EOS so the model learns to terminate after the response
        text = text + tokenizer.eos_token
        return tokenizer(
            text,
            truncation=True,
            max_length=args.max_length,
            padding=False,  # collator pads to batch max
        )

    train_ds = Dataset.from_list(train_rows).map(tokenize, remove_columns=["instruction", "response"])
    val_ds = Dataset.from_list(val_rows).map(tokenize, remove_columns=["instruction", "response"])
    # Causal LM — labels = input_ids, collator masks padding
    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    # ── Training ───────────────────────────────────────────────────────
    os.makedirs(args.output_dir, exist_ok=True)
    training_args = TrainingArguments(
        output_dir=args.output_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        weight_decay=0.01,
        warmup_ratio=0.03,
        lr_scheduler_type="cosine",
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        logging_steps=10,
        report_to="none",  # No W&B/MLflow — roadmap's ignore list
        seed=args.seed,
        # Mixed-precision when CUDA bf16 is supported (Ampere+). Falls
        # back to fp16 on older GPUs; ignored on CPU.
        bf16=device.type == "cuda" and torch.cuda.is_bf16_supported(),
        fp16=device.type == "cuda" and not torch.cuda.is_bf16_supported(),
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        data_collator=collator,
    )

    logger.info("starting training")
    trainer.train()

    # ── Save adapter (HF format, safetensors) ───────────────────────────
    logger.info("saving adapter to %s", args.output_dir)
    model.save_pretrained(args.output_dir, safe_serialization=True)
    tokenizer.save_pretrained(args.output_dir)
    # Drop a metadata file so operators can audit what produced the adapter
    with open(os.path.join(args.output_dir, "training_metadata.json"), "w") as f:
        json.dump({
            "base_model": args.base_model,
            "epochs": args.epochs,
            "lora_r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "target_modules": args.target_modules,
            "train_rows": len(train_rows),
            "val_rows": len(val_rows),
            "max_length": args.max_length,
            "learning_rate": args.learning_rate,
            "seed": args.seed,
        }, f, indent=2)

    logger.info("done. Restart the app (or call local_model.invalidate_cache()) "
                "for the adapter to pick up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

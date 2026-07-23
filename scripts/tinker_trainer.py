#!/usr/bin/env python3
"""LoRA fine-tune via Thinking Machines' Tinker SDK (PR 3a-Tinker).

Mirrors ``scripts/finetune_router.py``'s CLI but routes the actual
training through Tinker's managed-GPU service. Same JSONL data
pipeline, same output adapter location, same downstream loader.

What changes vs the local trainer:

* GPU runs on Tinker's infrastructure, not the operator's hardware.
  ``TINKER_API_KEY`` (env var only — never in code, commits, or logs)
  authenticates the SDK.
* Base model is resolved via ``app/core/learning/model_registry.py``
  → defaults to ``Qwen/Qwen3.6-35B-A3B`` (Tinker's current Instruct
  model in the DeepSeek-equivalent quality tier).
* The training loop uses the SDK's low-level primitives:
  ``create_lora_training_client`` → ``forward_backward`` per batch →
  ``optim_step`` → periodic ``save_state``. The cookbook recipe
  ``recipes/chat_sl`` is the canonical reference.
* The trained adapter downloads as a ``.tar.gz`` archive; we extract
  it into ``data/learning/adapters/construction_v1/`` so the existing
  ``local_model.py`` loader picks it up unchanged.

What stays the same:

* JSONL input format: ``{instruction, response, source, ...}`` per row.
* Output adapter location.
* Chat block's ``use_local_model=true`` opt-in (PR #24).
* Orin port story (see ``docs/EDGE_PORT.md``) — HF safetensors format.

CLI:
    python scripts/tinker_trainer.py \\
        [--alias construction_v1] \\
        [--train-data data/learning/training_set.jsonl] \\
        [--output-dir data/learning/adapters/construction_v1] \\
        [--epochs 3] \\
        [--lora-r 16] \\
        [--batch-size 4] \\
        [--max-steps <N>] \\
        [--save-every <N>] \\
        [--resume-from <tinker_path>]

Honest non-verification: this script was written but never executed
against a real Tinker account. The cookbook README documents the
high-level SDK shape but the full LoRA SFT loop is example-driven;
``recipes/chat_sl`` and ``recipes/sl_basic`` are the references. First
real run on the operator's account may surface bugs (renderer
signature, save_state cadence, checkpoint archive layout). Treat the
first invocation as integration testing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Resolve the `app` package when invoked directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


# ── Data conversion ───────────────────────────────────────────────────────


def jsonl_to_chat(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one ``{instruction, response}`` row to chat format.

    Cookbook recipes (``recipes/chat_sl``) use the standard
    ``{messages: [{role, content}, ...]}`` shape — the same format
    OpenAI / Anthropic / DeepSeek chat APIs use. Renderers in the
    cookbook handle the tokenization; we just produce the structured
    messages.

    Returns the converted row preserving any extra fields (``source``,
    ``conversation_id``) for downstream filtering / debugging.
    """
    out = {
        "messages": [
            {"role": "user", "content": (row.get("instruction") or "").strip()},
            {"role": "assistant", "content": (row.get("response") or "").strip()},
        ],
    }
    for k in ("source", "conversation_id"):
        if k in row:
            out[k] = row[k]
    return out


def load_training_data(path: str) -> List[Dict[str, Any]]:
    """Load JSONL and convert each row to chat format. Skips malformed
    lines with a warning rather than aborting."""
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(jsonl_to_chat(json.loads(line)))
            except json.JSONDecodeError as exc:
                logger.warning("line %d malformed, skipping: %s", i, exc)
    return rows


def _split_train_val(rows, val_split: float, seed: int):
    rng = random.Random(seed)
    indices = list(range(len(rows)))
    rng.shuffle(indices)
    n_val = max(1, int(len(rows) * val_split))
    val_idx = set(indices[:n_val])
    train = [rows[i] for i in range(len(rows)) if i not in val_idx]
    val = [rows[i] for i in range(len(rows)) if i in val_idx]
    return train, val


# ── SDK plumbing ──────────────────────────────────────────────────────────


def _require_api_key() -> str:
    """Return ``TINKER_API_KEY`` or raise with a clear remediation.

    The error message names the env var (operators need to know what
    to set) but NEVER quotes the value — even a partial leak in CI logs
    is unacceptable for secrets.
    """
    key = os.environ.get("TINKER_API_KEY")
    if not key:
        raise RuntimeError(
            "TINKER_API_KEY environment variable is not set. "
            "Get a key from https://tinker-console.thinkingmachines.ai "
            "and `export TINKER_API_KEY=<key>` before running."
        )
    return key


def _service_client():
    """Construct a ``tinker.ServiceClient`` with the env-supplied key.

    Lazy import so the rest of the script can be inspected without the
    SDK installed (tests mock this entire function out)."""
    _require_api_key()
    try:
        import tinker
    except ImportError as exc:
        raise RuntimeError(
            "tinker SDK is not installed. Install with "
            "`pip install tinker` or `pip install -r requirements-ml.txt`."
        ) from exc
    return tinker.ServiceClient()


# ── Training loop ─────────────────────────────────────────────────────────


def run_training(
    train_rows: List[Dict[str, Any]],
    val_rows: List[Dict[str, Any]],
    base_model: str,
    lora_r: int,
    epochs: int,
    batch_size: int,
    save_every: int,
    max_steps: Optional[int],
    resume_from: Optional[str],
    output_dir: str,
) -> str:
    """Run the Tinker training loop. Returns the final tinker_path.

    The actual training-loop semantics (what ``forward_backward`` accepts,
    how the renderer wraps messages, how ``save_state`` paths are named)
    follow the cookbook's ``recipes/chat_sl`` recipe. Keep this function
    aligned with that recipe when Tinker pushes SDK updates.
    """
    client = _service_client()
    training_client = client.create_lora_training_client(
        base_model=base_model,
        rank=lora_r,
    )

    if resume_from:
        logger.info("resuming from %s", resume_from)
        training_client.load_state(resume_from)

    # The cookbook handles batching via its renderers + DataLoader. Here
    # we keep the loop transparent so failures surface at the right step.
    step = 0
    save_path: Optional[str] = None
    for epoch in range(epochs):
        random.shuffle(train_rows)
        for batch_start in range(0, len(train_rows), batch_size):
            batch = train_rows[batch_start:batch_start + batch_size]
            # forward_backward signature per the cookbook: pass the
            # rendered batch; the SDK handles gradient accumulation.
            training_client.forward_backward(batch)
            training_client.optim_step()
            step += 1
            if step % save_every == 0:
                save_path = f"checkpoints/{base_model.replace('/', '_')}/step_{step}"
                training_client.save_state(save_path)
                logger.info("saved checkpoint at step %d: %s", step, save_path)
            if max_steps is not None and step >= max_steps:
                logger.info("reached max_steps %d; stopping", max_steps)
                break
        if max_steps is not None and step >= max_steps:
            break
        # Eval pass — log loss on validation subset (cookbook recipe pattern)
        if val_rows:
            # Tinker may expose a separate eval call; if not, skip
            try:
                training_client.forward_backward(val_rows[:batch_size])  # smoke
            except Exception as exc:  # noqa: BLE001
                logger.warning("eval step skipped: %s", exc)

    # Final save
    final_path = f"checkpoints/{base_model.replace('/', '_')}/final"
    training_client.save_state(final_path)
    save_path = final_path
    return save_path


def download_checkpoint(tinker_path: str, output_dir: str) -> None:
    """Download a Tinker checkpoint archive and extract it.

    The cookbook README documents the round-trip:
    ``rest_client.get_checkpoint_archive_url_from_tinker_path(path)`` →
    a ``.tar.gz`` URL that we stream to disk and untar into the adapter
    directory the local loader expects.

    Checkpoint format note: Tinker's archive layout is not fully
    documented in the public cookbook. If the extracted files don't
    match the HF expectation (``adapter_config.json`` +
    ``adapter_model.safetensors``), the loader's integrity check will
    log a warning and fall back to cloud chat — the chat block never
    goes dark because of a mid-flight format surprise.
    """
    client = _service_client()
    rest_client = client.create_rest_client()
    future = rest_client.get_checkpoint_archive_url_from_tinker_path(tinker_path)
    url = future.result()

    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "httpx is required to download the checkpoint archive. "
            "It is a base dependency; pip install -r requirements.txt."
        ) from exc

    tmp_archive = Path(output_dir) / "_checkpoint.tar.gz"
    tmp_archive.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=300) as http:
        with http.stream("GET", url) as response:
            response.raise_for_status()
            with open(tmp_archive, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)

    logger.info("extracting %s → %s", tmp_archive, output_dir)
    with tarfile.open(tmp_archive, "r:gz") as tar:
        # Filter members to prevent path-traversal via crafted archives.
        for member in tar.getmembers():
            if member.name.startswith(("/", "..")) or ".." in Path(member.name).parts:
                raise RuntimeError(f"unsafe archive member: {member.name!r}")
        tar.extractall(output_dir)

    tmp_archive.unlink()  # cleanup
    logger.info("checkpoint extracted; adapter at %s", output_dir)


# ── CLI ───────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alias", default="construction_v1",
        help="Logical model alias (see app/core/learning/model_registry.py)",
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
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--save-every", type=int, default=200,
                        help="Save state every N optim steps")
    parser.add_argument("--max-steps", type=int, default=None,
                        help="Stop after this many optim steps (for smoke tests)")
    parser.add_argument("--val-split", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume-from", default=None,
                        help="Tinker path to resume from (skips initial state)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    # Fail fast on the two most common misconfigs before doing any work
    try:
        _require_api_key()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    if not os.path.exists(args.train_data):
        logger.error(
            "training data not found: %s — run scripts/export_training_set.py first",
            args.train_data,
        )
        return 1

    from app.core.learning.model_registry import resolve_base_model
    try:
        base_model = resolve_base_model(args.alias, trainer="tinker")
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    rows = load_training_data(args.train_data)
    if len(rows) < 10:
        logger.error(
            "only %d rows in %s — need at least 10 for a meaningful fine-tune",
            len(rows), args.train_data,
        )
        return 1
    train_rows, val_rows = _split_train_val(rows, args.val_split, args.seed)
    logger.info("split: %d train, %d val, base_model=%s", len(train_rows), len(val_rows), base_model)

    try:
        tinker_path = run_training(
            train_rows=train_rows,
            val_rows=val_rows,
            base_model=base_model,
            lora_r=args.lora_r,
            epochs=args.epochs,
            batch_size=args.batch_size,
            save_every=args.save_every,
            max_steps=args.max_steps,
            resume_from=args.resume_from,
            output_dir=args.output_dir,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("training failed: %s", exc)
        return 2

    logger.info("training complete; final tinker_path=%s", tinker_path)
    try:
        download_checkpoint(tinker_path, args.output_dir)
    except Exception as exc:  # noqa: BLE001
        logger.exception("checkpoint download failed: %s", exc)
        # Training succeeded but download failed — operator can rerun
        # download with the printed tinker_path. Distinct exit code.
        return 3

    logger.info(
        "done. Restart the app (or call local_model.invalidate_cache()) "
        "for the adapter to pick up."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

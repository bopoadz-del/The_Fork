"""Model registry — maps logical names to current Tinker / HF model IDs.

When Tinker deprecates a model (they sunset older variants every few
months to keep their throughput/latency profile), only this file needs
updating. Both ``scripts/finetune_router.py`` (local trainer) and
``scripts/tinker_trainer.py`` (managed trainer) resolve their base model
through :func:`resolve_base_model` so the swap is one place.

Why two columns (``tinker`` vs ``local``)?

The trainers target different cost/scale tiers:
- Tinker runs on Thinking Machines' GPU pool — operators reach for the
  best model their budget supports. The DeepSeek-V3.1-Base substitute
  per Tinker's own deprecation notice is Qwen3.5-35B-A3B-Base; the
  current Instruct variant in the same quality tier is Qwen3.6-35B-A3B.
- Local runs on the operator's workstation/cloud GPU — usually a single
  consumer-class card. A 3B Instruct fits comfortably; 35B-A3B does not.
  Same architectural family (Qwen Instruct) keeps the adapter
  fine-tuned from the same prompt format usable across.

When the same fine-tuned adapter needs to work on both: ALWAYS pick the
"local" entry as the upstream base when training. Tinker adapters
trained on a 35B model won't load on a 3B base.
"""

from __future__ import annotations

from typing import Dict


# (logical_name) -> {"tinker": ..., "local": ...}
#
# Update this when Tinker's lineup changes. Each row's deprecation
# rationale should be commented inline so the next operator knows
# what the upgrade path was.
_REGISTRY: Dict[str, Dict[str, str]] = {
    # Construction-domain chat model. Tinker's substitute for
    # DeepSeek-V3.1-Base (per their May 2026 deprecation notice) is
    # Qwen3.5-35B-A3B-Base; the Instruct equivalent in the current
    # lineup is Qwen3.6-35B-A3B.
    #
    # Local default stays smaller — Qwen2.5-3B-Instruct fits on a
    # workstation card and runs at usable speeds without quantization.
    # Cross-trainer adapter portability is NOT guaranteed at these
    # different sizes; users picking Tinker for training should also
    # serve via Tinker (or quantize for the Orin per docs/EDGE_PORT.md).
    "construction_v1": {
        "tinker": "Qwen/Qwen3.6-35B-A3B",
        "local":  "Qwen/Qwen2.5-3B-Instruct",
    },
}


def resolve_base_model(logical_name: str, trainer: str) -> str:
    """Return the concrete model ID for ``(logical_name, trainer)``.

    ``trainer`` must be ``"tinker"`` or ``"local"``. Raises ValueError on
    an unknown logical name or trainer — silent fallbacks would hide
    real misconfigurations.
    """
    if logical_name not in _REGISTRY:
        raise ValueError(
            f"unknown model alias {logical_name!r}; "
            f"choices: {sorted(_REGISTRY.keys())}"
        )
    entry = _REGISTRY[logical_name]
    if trainer not in entry:
        raise ValueError(
            f"no {trainer!r} model registered for {logical_name!r}; "
            f"available trainers: {sorted(entry.keys())}"
        )
    return entry[trainer]


def list_aliases() -> Dict[str, Dict[str, str]]:
    """Return a copy of the registry for inspection / dashboards."""
    return {k: dict(v) for k, v in _REGISTRY.items()}

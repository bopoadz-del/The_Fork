"""Tests for the Tinker trainer scaffolding (PR 3a-Tinker).

Strategy:
- Mock ``tinker.ServiceClient`` and ``httpx.Client`` so no real Tinker
  API call or HTTP request happens. CI has zero billing exposure.
- Test the **scaffolding shape**: JSONL→chat conversion, SDK call
  sequence (create_lora_training_client → forward_backward → optim_step
  → save_state → checkpoint download), error paths.
- The actual SDK semantics (what forward_backward accepts in detail,
  how renderers wrap messages) ARE example-driven per Tinker's
  cookbook. First real run on the user's account is the integration
  test for those — this suite covers the parts we can verify here.

What this suite does NOT test:
- Real fine-tune output quality (no real Tinker call)
- Real checkpoint archive format (depends on what Tinker emits)
- Concurrent SDK access (single-threaded scaffolding)
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def with_api_key(monkeypatch):
    monkeypatch.setenv("TINKER_API_KEY", "fake-test-key-not-real")
    yield


@pytest.fixture
def without_api_key(monkeypatch):
    monkeypatch.delenv("TINKER_API_KEY", raising=False)
    yield


@pytest.fixture
def fake_tinker_module(monkeypatch):
    """Install a fake ``tinker`` module so the SDK import succeeds with
    no real package installed. Returns the call-recording MagicMocks."""
    fake_training_client = MagicMock()
    fake_training_client.forward_backward = MagicMock(return_value=None)
    fake_training_client.optim_step = MagicMock(return_value=None)
    fake_training_client.save_state = MagicMock(return_value=None)
    fake_training_client.load_state = MagicMock(return_value=None)

    fake_rest_client = MagicMock()
    fake_url_future = MagicMock()
    fake_url_future.result = MagicMock(return_value="https://example.invalid/ckpt.tar.gz")
    fake_rest_client.get_checkpoint_archive_url_from_tinker_path = MagicMock(
        return_value=fake_url_future
    )

    fake_service_client = MagicMock()
    fake_service_client.create_lora_training_client = MagicMock(return_value=fake_training_client)
    fake_service_client.create_rest_client = MagicMock(return_value=fake_rest_client)

    fake_tinker = SimpleNamespace(
        ServiceClient=MagicMock(return_value=fake_service_client),
    )
    monkeypatch.setitem(sys.modules, "tinker", fake_tinker)
    return {
        "tinker": fake_tinker,
        "service": fake_service_client,
        "training": fake_training_client,
        "rest": fake_rest_client,
    }


# ── Data conversion ──────────────────────────────────────────────────────


def test_jsonl_to_chat_basic_shape():
    """{instruction, response} converts to {messages: [{user},{assistant}]}."""
    from scripts.tinker_trainer import jsonl_to_chat

    row = {"instruction": "what is concrete cover?", "response": "typically 25-40mm"}
    chat = jsonl_to_chat(row)
    assert chat["messages"] == [
        {"role": "user", "content": "what is concrete cover?"},
        {"role": "assistant", "content": "typically 25-40mm"},
    ]


def test_jsonl_to_chat_preserves_metadata():
    """Extra fields like `source` and `conversation_id` are carried
    through for debugging / filtering. The Tinker SDK ignores unknown
    keys; we just want to keep them for our own use."""
    from scripts.tinker_trainer import jsonl_to_chat

    row = {
        "instruction": "x",
        "response": "y",
        "source": "agent_memory",
        "conversation_id": "c123",
    }
    chat = jsonl_to_chat(row)
    assert chat["source"] == "agent_memory"
    assert chat["conversation_id"] == "c123"


def test_jsonl_to_chat_handles_missing_fields():
    """Robust to upstream malformed rows — empty strings rather than
    KeyErrors so the loader can skip silently."""
    from scripts.tinker_trainer import jsonl_to_chat

    chat = jsonl_to_chat({"instruction": "only the user side"})
    assert chat["messages"][0]["content"] == "only the user side"
    assert chat["messages"][1]["content"] == ""


def test_load_training_data_skips_malformed_lines(tmp_path):
    """One bad line doesn't abort the load — operator gets a warning."""
    from scripts.tinker_trainer import load_training_data

    path = tmp_path / "training.jsonl"
    path.write_text(
        '{"instruction": "q1", "response": "a1"}\n'
        'NOT JSON\n'
        '{"instruction": "q2", "response": "a2"}\n'
    )
    rows = load_training_data(str(path))
    assert len(rows) == 2
    assert rows[0]["messages"][0]["content"] == "q1"
    assert rows[1]["messages"][0]["content"] == "q2"


# ── API key handling ────────────────────────────────────────────────────


def test_require_api_key_raises_when_missing(without_api_key):
    """The error message must name the env var (operators need to know
    what to set) but NEVER quote any value — even a partial leak in CI
    logs is unacceptable for secrets."""
    from scripts.tinker_trainer import _require_api_key

    with pytest.raises(RuntimeError) as excinfo:
        _require_api_key()
    msg = str(excinfo.value)
    assert "TINKER_API_KEY" in msg, "error must name the env var"
    # The fake test value should NOT appear in the error (it shouldn't
    # have been read — this is the missing case — but defensive check)
    assert "fake" not in msg.lower()


def test_require_api_key_returns_when_present(with_api_key):
    from scripts.tinker_trainer import _require_api_key
    assert _require_api_key() == "fake-test-key-not-real"


def test_main_returns_nonzero_when_api_key_missing(without_api_key, tmp_path):
    """End-to-end: invoking the script without the env var exits with
    code 1 and surfaces the env var name in stderr."""
    out = tmp_path / "x.jsonl"
    out.write_text('{"instruction": "x", "response": "y"}\n')
    result = subprocess.run(
        [sys.executable, "scripts/tinker_trainer.py", "--train-data", str(out)],
        capture_output=True, text=True, cwd=Path.cwd(),
        env={k: v for k, v in os.environ.items() if k != "TINKER_API_KEY"},
    )
    assert result.returncode == 1
    assert "TINKER_API_KEY" in (result.stderr + result.stdout)


# ── Training loop call sequence ─────────────────────────────────────────


def test_run_training_calls_lora_client_with_resolved_model(with_api_key, fake_tinker_module):
    """create_lora_training_client is called with the registry-resolved
    base model + rank from --lora-r. Verifies the SDK constructor
    signature alignment."""
    from scripts.tinker_trainer import run_training

    rows = [{"messages": [{"role": "user", "content": "q"},
                           {"role": "assistant", "content": "a"}]} for _ in range(8)]
    run_training(
        train_rows=rows, val_rows=[],
        base_model="Qwen/Qwen3.6-35B-A3B", lora_r=8,
        epochs=1, batch_size=4, save_every=999, max_steps=1,
        resume_from=None, output_dir="/tmp/test_out",
    )
    fake_tinker_module["service"].create_lora_training_client.assert_called_once_with(
        base_model="Qwen/Qwen3.6-35B-A3B",
        rank=8,
    )


def test_run_training_calls_forward_backward_and_optim_step_per_batch(
    with_api_key, fake_tinker_module
):
    """For each batch the loop calls forward_backward then optim_step in
    that order. Mirrors the cookbook's recipes/chat_sl pattern."""
    from scripts.tinker_trainer import run_training

    rows = [{"messages": [{"role": "user", "content": "q"},
                           {"role": "assistant", "content": "a"}]} for _ in range(12)]
    run_training(
        train_rows=rows, val_rows=[],
        base_model="x", lora_r=8,
        epochs=1, batch_size=4, save_every=999, max_steps=None,
        resume_from=None, output_dir="/tmp/test_out",
    )
    training = fake_tinker_module["training"]
    # 12 rows / batch_size 4 = 3 batches per epoch
    assert training.forward_backward.call_count >= 3
    assert training.optim_step.call_count >= 3
    # save_state called once at minimum (final save)
    assert training.save_state.call_count >= 1


def test_run_training_respects_max_steps(with_api_key, fake_tinker_module):
    """--max-steps caps the inner loop. Important for smoke tests
    against billable services — operators do a 5-step run first."""
    from scripts.tinker_trainer import run_training

    rows = [{"messages": [{"role": "user", "content": "q"},
                           {"role": "assistant", "content": "a"}]} for _ in range(100)]
    run_training(
        train_rows=rows, val_rows=[],
        base_model="x", lora_r=8,
        epochs=10, batch_size=4, save_every=999, max_steps=3,
        resume_from=None, output_dir="/tmp/test_out",
    )
    # max_steps=3 means forward_backward should run 3 times for train +
    # at most 1 for eval. We test the cap rather than exact count.
    assert fake_tinker_module["training"].optim_step.call_count == 3


def test_run_training_save_state_cadence(with_api_key, fake_tinker_module):
    """save_state fires every --save-every steps + once at the end."""
    from scripts.tinker_trainer import run_training

    rows = [{"messages": [{"role": "user", "content": "q"},
                           {"role": "assistant", "content": "a"}]} for _ in range(40)]
    run_training(
        train_rows=rows, val_rows=[],
        base_model="x", lora_r=8,
        epochs=1, batch_size=4, save_every=2, max_steps=8,
        resume_from=None, output_dir="/tmp/test_out",
    )
    # 8 steps / save_every 2 = 4 intermediate saves + 1 final = 5
    assert fake_tinker_module["training"].save_state.call_count == 5


def test_run_training_resume_from_calls_load_state(with_api_key, fake_tinker_module):
    """--resume-from triggers load_state with the provided path before
    the loop starts."""
    from scripts.tinker_trainer import run_training

    rows = [{"messages": [{"role": "user", "content": "q"},
                           {"role": "assistant", "content": "a"}]} for _ in range(4)]
    run_training(
        train_rows=rows, val_rows=[],
        base_model="x", lora_r=8,
        epochs=1, batch_size=4, save_every=999, max_steps=1,
        resume_from="checkpoints/prior/step_500", output_dir="/tmp/test_out",
    )
    fake_tinker_module["training"].load_state.assert_called_once_with(
        "checkpoints/prior/step_500"
    )


# ── Checkpoint download + extraction ─────────────────────────────────────


def _make_fake_archive(tmp_path: Path, members: List[str]) -> bytes:
    """Build a valid in-memory .tar.gz with the given member filenames."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in members:
            data = f"contents of {name}".encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_download_checkpoint_extracts_to_output_dir(
    with_api_key, fake_tinker_module, tmp_path, monkeypatch,
):
    """Happy path: REST URL fetched, tar.gz streamed + extracted, files
    end up in the output directory."""
    from scripts import tinker_trainer

    archive_bytes = _make_fake_archive(
        tmp_path, ["adapter_config.json", "adapter_model.safetensors"]
    )

    # Mock httpx.Client.stream to return our archive bytes
    fake_stream_resp = MagicMock()
    fake_stream_resp.raise_for_status = MagicMock()
    fake_stream_resp.iter_bytes = MagicMock(return_value=iter([archive_bytes]))

    fake_stream_ctx = MagicMock()
    fake_stream_ctx.__enter__ = MagicMock(return_value=fake_stream_resp)
    fake_stream_ctx.__exit__ = MagicMock(return_value=False)

    fake_http_client = MagicMock()
    fake_http_client.stream = MagicMock(return_value=fake_stream_ctx)
    fake_http_ctx = MagicMock()
    fake_http_ctx.__enter__ = MagicMock(return_value=fake_http_client)
    fake_http_ctx.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_ctx))

    out_dir = tmp_path / "adapter_out"
    tinker_trainer.download_checkpoint("checkpoints/x/final", str(out_dir))

    # The extracted files should land at out_dir/
    assert (out_dir / "adapter_config.json").exists()
    assert (out_dir / "adapter_model.safetensors").exists()
    # And the temp archive should be cleaned up
    assert not (out_dir / "_checkpoint.tar.gz").exists()


def test_download_checkpoint_rejects_unsafe_paths(
    with_api_key, fake_tinker_module, tmp_path, monkeypatch,
):
    """Path traversal in a crafted archive must raise rather than
    write outside the output directory."""
    from scripts import tinker_trainer

    archive_bytes = _make_fake_archive(tmp_path, ["../escape.txt"])

    fake_stream_resp = MagicMock()
    fake_stream_resp.raise_for_status = MagicMock()
    fake_stream_resp.iter_bytes = MagicMock(return_value=iter([archive_bytes]))
    fake_stream_ctx = MagicMock()
    fake_stream_ctx.__enter__ = MagicMock(return_value=fake_stream_resp)
    fake_stream_ctx.__exit__ = MagicMock(return_value=False)
    fake_http_client = MagicMock()
    fake_http_client.stream = MagicMock(return_value=fake_stream_ctx)
    fake_http_ctx = MagicMock()
    fake_http_ctx.__enter__ = MagicMock(return_value=fake_http_client)
    fake_http_ctx.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_http_ctx))

    with pytest.raises(RuntimeError, match="unsafe archive member"):
        tinker_trainer.download_checkpoint("checkpoints/x/final", str(tmp_path / "out"))


# ── Model registry ───────────────────────────────────────────────────────


def test_registry_resolves_construction_v1_for_tinker():
    """The user's locked direction: Tinker default is Qwen3.6-35B-A3B."""
    from app.core.learning.model_registry import resolve_base_model
    assert resolve_base_model("construction_v1", trainer="tinker") == "Qwen/Qwen3.6-35B-A3B"


def test_registry_resolves_construction_v1_for_local():
    """Local trainer default stays smaller — fits a workstation GPU."""
    from app.core.learning.model_registry import resolve_base_model
    assert resolve_base_model("construction_v1", trainer="local") == "Qwen/Qwen2.5-3B-Instruct"


def test_registry_unknown_alias_raises():
    """Silent fallbacks would hide misconfigurations. Be loud."""
    from app.core.learning.model_registry import resolve_base_model
    with pytest.raises(ValueError, match="unknown model alias"):
        resolve_base_model("not_a_real_alias", trainer="tinker")


def test_registry_unknown_trainer_raises():
    from app.core.learning.model_registry import resolve_base_model
    with pytest.raises(ValueError, match="no .* model registered"):
        resolve_base_model("construction_v1", trainer="bogus_trainer")


# ── finetune_router --trainer dispatch ───────────────────────────────────


def test_finetune_router_dispatches_to_tinker(with_api_key, monkeypatch, tmp_path):
    """When --trainer tinker is passed, finetune_router.py forwards to
    tinker_trainer.main rather than running the local Trainer."""
    from scripts import finetune_router

    train_data = tmp_path / "training.jsonl"
    train_data.write_text(
        "\n".join(json.dumps({"instruction": f"q{i}", "response": f"a{i}"}) for i in range(20))
    )

    # Capture what gets forwarded
    captured = {"argv": None}
    def fake_tinker_main(argv):
        captured["argv"] = argv
        return 42  # arbitrary success-ish code we can assert on
    monkeypatch.setattr("scripts.tinker_trainer.main", fake_tinker_main)

    rc = finetune_router.main_from_args([
        "--trainer", "tinker",
        "--train-data", str(train_data),
        "--output-dir", str(tmp_path / "out"),
        "--epochs", "1",
        "--lora-r", "8",
    ])
    assert rc == 42
    assert captured["argv"] is not None
    # The forwarded args should include the resolved base model alias
    assert "--alias" in captured["argv"]
    assert "construction_v1" in captured["argv"]

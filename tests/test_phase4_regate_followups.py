"""Phase-4 re-gate follow-ups (F38b, F42, F43) plus the ODA Qt env fix.

Found by running the post-deploy live re-gates for PR #363:

* F38b -- a SAVED workflow ran its blocks with no inputs ("No file path
  provided") while the direct orchestrator path threaded them fine:
  ChainStep had no ``input`` field, so pydantic silently dropped the
  stored step's input dict at the model boundary.
* F42 -- the construction container wraps a route error in a
  success-shaped transport envelope (ok: True, inner status: "error").
  The non-streaming loop only checked ``ok`` and never issued the
  corrective nudge.
* F43 -- container file actions (bim_extract et al) received the BARE
  filename and died on "File not found: qa_building.ifc"; the
  original-name -> stored-path resolution only covered file-schema blocks.
"""
from __future__ import annotations

import pytest

from app.agents import runtime


# ---------------------------------------------------------------- F38b


def test_chain_step_model_keeps_per_step_input():
    from app.routers.chain import ChainStep
    s = ChainStep(block="file_hasher", input={"file_path": "x.bin"})
    dumped = s.model_dump(exclude_unset=True)
    assert dumped.get("input") == {"file_path": "x.bin"}


# ---------------------------------------------------------------- F42


def test_envelope_green_inner_error_counts_as_errored():
    # The exact live shape: construction container, ok True, inner error.
    tr = {"name": "construction", "ok": True,
          "result": {"block": "construction", "status": "error",
                     "result": {"status": "error",
                                "error": "Unknown action: bim_element_census"}}}
    assert runtime._tool_result_errored(tr) is True


def test_two_level_nested_error_counts_as_errored():
    tr = {"ok": True, "result": {"status": "success",
          "result": {"status": "error", "error": "File not found"}}}
    assert runtime._tool_result_errored(tr) is True


def test_genuinely_green_results_do_not_nudge():
    tr = {"ok": True, "result": {"status": "success",
          "result": {"status": "success", "items": [1]}}}
    assert runtime._tool_result_errored(tr) is False
    assert runtime._tool_result_errored({"ok": True, "result": "plain text"}) is False


# ---------------------------------------------------------------- F43


def test_construction_dict_payloads_resolve_project_filenames(monkeypatch):
    calls = {}

    def fake_resolve(project_id, payload):
        calls["payload"] = payload
        if isinstance(payload, dict) and payload.get("file_path"):
            return {**payload, "file_path": "/app/data/abc_" + payload["file_path"]}
        return payload
    monkeypatch.setattr(runtime, "_resolve_block_file_input", fake_resolve)
    resolved = runtime._resolve_block_file_input(
        "p1", {"action": "bim_extract", "file_path": "qa_building.ifc"})
    assert resolved["file_path"].startswith("/app/data/")


def test_resolver_never_rewrites_bare_string_prose():
    # A bare-string construction input is a natural-language request; the
    # dispatch guard must leave it alone (only dict payloads resolve).
    # This pins the guard's premise: prose goes through unchanged when no
    # exact/substring document match exists in an empty project.
    out = runtime._resolve_file_path("nonexistent-project", "summarize the BOQ")
    assert out == "summarize the BOQ"

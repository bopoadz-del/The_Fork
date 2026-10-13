"""A document read continues as numbers, not as an instruction.

A long read comes back as a window of the document. The next window is a
offset and a count of characters still unread. Nothing in the result tells
the user to call a tool again, and the reply the model sees is that window,
not the truncation note.
"""
from __future__ import annotations

import asyncio
import json

from app.agents.base.tools import handle_fetch_document
from app.agents.core.tool_registry import ToolCall
from app.agents.runtime import _tool_result_content

_INSTRUCTION = (
    "call this tool again",
    "tool result exceeded",
    "file_path and char_offset",
    "char_offset",
)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _fetch(text, *, truncated=False):
    def fake(project_id, doc_id, filename):
        return (
            {"text": text, "truncated": truncated, "source": "stored"},
            {"id": "doc-1", "original_name": "specification.pdf", "doc_type": "pdf"},
            None,
        )
    return fake


def _call(args):
    return ToolCall(
        agent=None, name="fetch_document", args=args,
        project_id="job-1", tool_call={"function": {"name": "fetch_document", "arguments": args}},
    )


def test_a_long_read_returns_a_window_and_a_structured_continuation(monkeypatch):
    from app.agents import runtime

    text = "alpha " * 4000
    monkeypatch.setattr(runtime, "_fetch_document_content", _fetch(text))
    out = asyncio.run(handle_fetch_document(_call({"document_id": "doc-1"})))
    result = out["result"]
    assert result["content"]
    assert result["content"] == text[:len(result["content"])]
    assert result["truncated"] is True
    assert result["text_offset"] == 0
    assert isinstance(result["next_text_offset"], int) and result["next_text_offset"] > 0
    assert result["chars_remaining"] == len(text) - result["next_text_offset"]
    for blob in _strings(result):
        low = blob.lower()
        assert not any(phrase in low for phrase in _INSTRUCTION), blob[:180]
    shown = _tool_result_content(result, offset=0)
    assert "call this tool again" not in shown.lower()
    assert json.loads(shown)["content"] == result["content"]


def test_the_next_window_is_the_following_text(monkeypatch):
    from app.agents import runtime

    text = "alpha " * 4000
    monkeypatch.setattr(runtime, "_fetch_document_content", _fetch(text))
    first = asyncio.run(handle_fetch_document(_call({"document_id": "doc-1"})))["result"]
    second = asyncio.run(handle_fetch_document(_call({
        "document_id": "doc-1", "text_offset": first["next_text_offset"],
    })))["result"]
    assert second["content"] == text[first["next_text_offset"]:first["next_text_offset"] + len(second["content"])]
    assert first["content"] + second["content"] == text[:len(first["content"]) + len(second["content"])]
    assert second["text_offset"] == first["next_text_offset"]
    for blob in _strings(second):
        assert "call this tool again" not in blob.lower()


def test_a_short_read_has_nothing_left(monkeypatch):
    from app.agents import runtime

    text = "The cover is 40 mm."
    monkeypatch.setattr(runtime, "_fetch_document_content", _fetch(text))
    result = asyncio.run(handle_fetch_document(_call({"filename": "specification.pdf"})))["result"]
    assert result["content"] == text
    assert result["truncated"] is False
    assert result["chars_remaining"] == 0
    assert result["next_text_offset"] is None
    assert result["text_offset"] == 0

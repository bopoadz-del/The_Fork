"""An over-long tool result must stay VALID JSON.

LIVE 2026-08-03, feature sweep against prod — two different actions, same
hard provider rejection:

    document_metadata        -> kimi HTTP 400
    parse_primavera_schedule -> kimi HTTP 400
    {"message": "Invalid request: tokenization failed",
     "type": "invalid_request_error"}

Cause: the `tool` message content was built as

    json.dumps(result, default=str)[:8000]

`json.dumps` emits ASCII with \\uXXXX escapes, so that raw slice can land
mid-escape or mid-string. The corpus makes it easy to hit — the client
filenames contain en dashes, which serialize to `\\u2013`:

    tail of cut: ' 2 \\u2013 specification (4 of 9).pdf", "'
    json.loads  -> Unterminated string starting at ... (char 7999)

The provider got malformed JSON and refused the whole request. Because a
failed turn also persisted nothing, the user just saw a question with no
answer (see tests/test_failed_turn_persistence.py).
"""
from __future__ import annotations

import json

import pytest

from app.agents.runtime import _TOOL_RESULT_MAX_CHARS, _tool_result_content


def _en_dash_docs(n: int) -> dict:
    """The shape that actually broke it — en dashes become \\u2013."""
    return {
        "documents": [
            {"name": f"dd-2023-118 vol 2 – specification ({i} of 9).pdf",
             "type": "pdf"}
            for i in range(n)
        ]
    }


def test_small_result_is_passed_through_unchanged():
    payload = {"documents": [{"name": "a.pdf"}]}
    assert json.loads(_tool_result_content(payload)) == payload


def test_oversized_result_is_still_valid_json():
    """The load-bearing assertion. The old code failed exactly here."""
    out = _tool_result_content(_en_dash_docs(300))

    parsed = json.loads(out)  # must not raise
    assert parsed["truncated"] is True


def test_the_old_raw_slice_really_did_produce_invalid_json():
    """Pins WHY this helper exists, so nobody 'simplifies' it back.

    If this ever stops raising, the escape-splitting hazard is gone and the
    helper could be revisited — but that is a deliberate decision, not an
    accident.
    """
    raw = json.dumps(_en_dash_docs(300), default=str)[:_TOOL_RESULT_MAX_CHARS]

    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)


def test_truncated_result_stays_within_the_cap():
    out = _tool_result_content(_en_dash_docs(500))
    assert len(out) <= _TOOL_RESULT_MAX_CHARS


def test_truncation_is_announced_to_the_model():
    """Silent truncation would let the model present a partial list as whole."""
    parsed = json.loads(_tool_result_content(_en_dash_docs(300)))

    assert parsed["truncated"] is True
    assert "truncated" in parsed["note"].lower()
    assert parsed["preview"]


def test_non_serialisable_values_do_not_explode():
    """default=str must survive on both the small and truncated paths."""
    class Weird:
        def __repr__(self): return "<weird>"

    small = json.loads(_tool_result_content({"v": Weird()}))
    assert isinstance(small["v"], str)

    big = json.loads(_tool_result_content({"v": [Weird()] * 4000}))
    assert big["truncated"] is True


@pytest.mark.parametrize("n", [40, 120, 400, 1200])
def test_output_always_parses_across_sizes(n):
    """Sweep sizes so the cut lands at many different offsets — including
    inside an escape sequence, which is the whole hazard."""
    json.loads(_tool_result_content(_en_dash_docs(n)))

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


# -- what the truncation SAYS, not just that it stays parseable ------------
#
# The tests above pin that an over-long result stays valid JSON. They do not
# pin what the model is told about the cut, and that turned out to matter as
# much. Live on 554e0b9: asked how many cubic metres of demolition are in the
# BOQ, the model answered "(pages 1-6 of 16) ... no demolition line items
# measured in cubic metres". The 945 m3 line is past the cut. It had been
# told only that the result "exceeded 8000 characters and was truncated" --
# not by how much -- so it inferred the extent from the content and inferred
# it wrong.
#
# Two layers cut the same result: this one when the tool message is built,
# and _compact_messages_for_tpm per Groq hop (tests/test_tpm_compaction.py).
# Both now state the loss and both forbid the absence claim.


def test_the_envelope_states_the_loss_as_a_number():
    parsed = json.loads(_tool_result_content(_en_dash_docs(300)))
    assert parsed["chars_total"] > parsed["chars_shown"]
    assert parsed["chars_dropped"] == parsed["chars_total"] - parsed["chars_shown"]
    assert parsed["chars_shown"] == len(parsed["preview"])


def test_the_envelope_forbids_the_absence_claim():
    """inject.py has applied SCOPE OF ABSENCE to retrieval excerpts since the
    Saudi Building Code incident, because a partial view is evidence of what
    IS present and never of what is absent. A truncated tool result is the
    same shape of evidence and carries more authority, being the tool's own
    output rather than a search hit."""
    parsed = json.loads(_tool_result_content(_en_dash_docs(300)))
    rule = parsed["scope_of_absence"]
    assert "NEVER state" in rule
    assert "absent" in rule
    assert str(parsed["chars_shown"]) in rule
    assert str(parsed["chars_total"]) in rule


def test_the_numbers_survive_the_shrink_loop():
    """keep is reduced until the ENVELOPE fits, so chars_shown must track the
    final preview rather than the first attempt -- otherwise the model is
    told it can see more than it can."""
    for n in (300, 500, 900):
        parsed = json.loads(_tool_result_content(_en_dash_docs(n)))
        assert parsed["chars_shown"] == len(parsed["preview"])
        assert str(parsed["chars_shown"]) in parsed["scope_of_absence"]


def test_a_small_result_carries_no_scope_note():
    """Nothing was lost, so there is nothing to warn about -- and a warning
    on a complete result would teach the model to hedge answers it can
    fully support."""
    out = _tool_result_content({"documents": [{"name": "a.pdf"}]})
    assert "scope_of_absence" not in out
    assert "chars_dropped" not in out

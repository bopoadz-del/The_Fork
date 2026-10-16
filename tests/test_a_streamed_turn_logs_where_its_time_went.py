"""A streamed turn logs where its time went (order 0520Z item 1).

The driver turn's own timer missed time spent around it; lanes measured
45 s where the turn logged 22-34 s. Each streamed turn now logs one
TURN_TIMING line: milliseconds from receipt to its first event, first
token, end event and close. No content.
"""
import json
import logging

from app.agents.core import loop


def test_the_timing_line_has_every_mark_in_order(caplog):
    marks = {"received": 100.0, "first_event": 100.5, "first_token": 101.0, "end_event": 103.0}
    with caplog.at_level(logging.INFO, logger="app.agents.core.loop"):
        loop._log_turn_timing(marks, "conv-1")
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("TURN_TIMING"))
    data = json.loads(line.split("TURN_TIMING ", 1)[1])
    assert data["conversation"] == "conv-1"
    assert data["first_event"] == 500 and data["first_token"] == 1000 and data["end_event"] == 3000
    assert "closed" in data


def test_a_timing_failure_never_breaks_a_turn():
    loop._log_turn_timing({}, None)
    loop._log_turn_timing({"received": "not a number"}, "c")

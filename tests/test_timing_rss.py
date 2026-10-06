"""TIMING log lines carry the process's resident memory."""
import logging


def test_rss_is_read_from_the_process():
    from app.agents.runtime import _rss_mb

    assert _rss_mb() > 0


def test_every_timing_line_ends_with_rss(caplog):
    from app.agents.runtime import _timing_log

    with caplog.at_level(logging.WARNING, logger="app.agents.runtime"):
        _timing_log("TIMING chat_stream iter=%d call=%.1fs", 2, 1.5)
    (rec,) = [r for r in caplog.records if r.getMessage().startswith("TIMING")]
    msg = rec.getMessage()
    assert msg.startswith("TIMING chat_stream iter=2 call=1.5s rss=") and msg.endswith("MB")

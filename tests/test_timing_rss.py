"""TIMING log lines carry the process's resident memory."""


def test_rss_is_read_from_the_process():
    from app.agents.runtime import _rss_mb

    assert _rss_mb() > 0


def test_every_timing_line_ends_with_rss(monkeypatch):
    from app.agents import runtime as rt

    calls = []
    monkeypatch.setattr(rt._LOG, "warning", lambda fmt, *args: calls.append(fmt % args))
    rt._timing_log("TIMING chat_stream iter=%d call=%.1fs", 2, 1.5)
    (msg,) = calls
    assert msg.startswith("TIMING chat_stream iter=2 call=1.5s rss=") and msg.endswith("MB")

"""The visual surfaces: the loop's event stream, the MCP progress callback, and
the terminal demo. All deterministic, no TTY required."""
import asyncio

from helpers import DepthReasoner, stub_ground

from descartes import render
from descartes.loop import run_doubt_loop
from descartes.server import _make_on_event

run = asyncio.run


# --------------------------------------------------------------------------- #
# the loop emits a progress event stream
# --------------------------------------------------------------------------- #
def test_loop_emits_event_stream():
    types = []

    async def collect(e):
        types.append(e["type"])

    run(run_doubt_loop("task", "evidence", 20, DepthReasoner(depth=2), stub_ground, on_event=collect))
    assert types[0] == "start"
    assert types[-1] == "done"
    for needed in ("draft", "pass", "thinking", "doubt"):
        assert needed in types


def test_doubt_event_has_status_and_depth():
    events = []

    async def collect(e):
        if e["type"] == "doubt":
            events.append(e)

    run(run_doubt_loop("task", "evidence", 20, DepthReasoner(depth=1), stub_ground, on_event=collect))
    assert events and {"status", "depth", "operator", "doubt"} <= set(events[0])


def test_on_event_failure_never_breaks_the_loop():
    async def boom(e):
        raise RuntimeError("renderer died mid-doubt")

    res = run(run_doubt_loop("task", "evidence", 20, DepthReasoner(depth=1), stub_ground, on_event=boom))
    assert res["converged"] is True


def test_loop_without_on_event_is_unchanged():
    res = run(run_doubt_loop("task", "evidence", 20, DepthReasoner(depth=2), stub_ground))
    assert res["converged"] is True and res["passes_used"] == 3


# --------------------------------------------------------------------------- #
# the MCP progress/log callback (server side)
# --------------------------------------------------------------------------- #
def test_make_on_event_none_without_ctx_or_verbose():
    assert _make_on_event(None) is None


def test_make_on_event_maps_events_to_ctx():
    calls = {"progress": 0, "info": 0}

    class Ctx:
        async def report_progress(self, *a, **k):
            calls["progress"] += 1

        async def info(self, *a, **k):
            calls["info"] += 1

    oe = _make_on_event(Ctx())
    assert oe is not None
    run(oe({"type": "pass", "pass": 1, "cap": 20}))
    run(oe({"type": "draft", "plan": "x"}))
    run(oe({"type": "doubt", "depth": 0, "status": "CONFIRMED", "doubt": "q"}))
    run(oe({"type": "done", "converged": True, "passes": 2, "grounded": 3, "needs_user": 1}))
    assert calls["progress"] == 1          # one report_progress per pass
    assert calls["info"] == 3              # draft + doubt + done


def test_make_on_event_swallows_ctx_errors():
    class Ctx:
        async def report_progress(self, *a, **k):
            raise RuntimeError("client has no progress support")

        async def info(self, *a, **k):
            raise RuntimeError("client has no logging support")

    oe = _make_on_event(Ctx())
    run(oe({"type": "pass", "pass": 1, "cap": 20}))   # must not raise
    run(oe({"type": "doubt", "depth": 0, "status": "CONFIRMED", "doubt": "q"}))


def test_verbose_emits_to_stderr_without_ctx(monkeypatch, capsys):
    monkeypatch.setenv("DESCARTES_VERBOSE", "1")
    oe = _make_on_event(None)
    assert oe is not None                  # verbose alone is enough to observe
    run(oe({"type": "done", "converged": True, "passes": 2, "grounded": 5, "needs_user": 2}))
    err = capsys.readouterr().err
    assert "[descartes]" in err and "converged=True" in err


def test_doubt_tool_never_writes_to_stdout(monkeypatch, capsys):
    # stdout IS the MCP stdio protocol channel; the tool must keep it pristine,
    # even with verbose on and even when the client lacks progress/log support.
    monkeypatch.setenv("DESCARTES_VERBOSE", "1")
    from helpers import claude_sampling_ctx

    from descartes.server import doubt
    run(doubt("Design the cache layer.", "report.py builds ReportModel.", ctx=claude_sampling_ctx()))
    captured = capsys.readouterr()
    assert captured.out == ""              # nothing on stdout — protocol stays clean
    assert "[descartes]" in captured.err   # the visual went to stderr instead


# --------------------------------------------------------------------------- #
# the terminal demo
# --------------------------------------------------------------------------- #
def test_demo_runs_and_tells_the_story():
    res = render.run_demo()                # non-TTY in tests -> fast, no escapes
    assert res["converged"] is True
    assert res["max_depth_reached"] == 1   # recursion: it doubts its own answers
    grounded = sum(1 for e in res["doubt_log"] if e["status"] in ("CONFIRMED", "REFUTED"))
    assert grounded == 5
    assert len(res["needs_user"]) == 2

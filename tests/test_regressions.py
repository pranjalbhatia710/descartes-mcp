"""Regression guards — one per bug confirmed by the adversarial audit.

Each test fails on the pre-fix code and passes on the fixed code, so the audit
findings stay fixed.
"""
import asyncio

from helpers import (
    FakeResp,
    ScriptedReasoner,  # noqa: E402  (after package imports)
    install_fake_httpx,
)

from descartes.grounding import _coerce_score, ground
from descartes.loop import PRUNE_SYS, prune_doubts
from descartes.panel import FireworksPanel, select_reasoner
from descartes.server import compute_verdict

run = asyncio.run


# Bug 1 — prune: a reworded doubt sharing the first 120 normalized chars must be dropped.
def test_prune_rejects_prefix_collision_reword():
    base = "x" * 120  # two distinct doubts identical for the first 120 chars
    d1 = {"operator": "assumption", "doubt": f"{base} alpha?", "kind": "code"}
    d2 = {"operator": "assumption", "doubt": f"{base} beta?", "kind": "code"}
    reworded = f'[{{"operator":"assumption","doubt":"{base} gamma?","kind":"code"}}]'
    r = ScriptedReasoner({PRUNE_SYS: [reworded]})
    survivors = run(prune_doubts(r, [d1, d2], "plan"))
    assert survivors == []  # gamma is neither alpha nor beta -> not verbatim -> dropped


# Bug 2 — misconfigured DESCARTES_PANEL_SIZE must degrade, not crash.
def test_bad_panel_size_env_does_not_crash(monkeypatch):
    monkeypatch.setenv("DESCARTES_PANEL_SIZE", "not-a-number")
    p = FireworksPanel("fw")                 # would raise ValueError pre-fix
    assert p.panel_size >= 1
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw")
    assert isinstance(select_reasoner(object()), FireworksPanel)  # selection still works


# Bug 3 — misconfigured DESCARTES_TIMEOUT must be floored/defaulted, never <= 0
# and never crash. (Tested via the parse helper to avoid module-reload footguns.)
def test_bad_timeout_env_is_floored(monkeypatch):
    from descartes.panel import _env_float, _env_int
    monkeypatch.setenv("DESCARTES_TIMEOUT", "0")
    assert _env_float("DESCARTES_TIMEOUT", 45, minimum=1.0) == 1.0   # floored
    monkeypatch.setenv("DESCARTES_TIMEOUT", "garbage")
    assert _env_float("DESCARTES_TIMEOUT", 45, minimum=1.0) == 45.0  # bad -> default
    monkeypatch.setenv("DESCARTES_MAX_TOKENS", "oops")
    assert _env_int("DESCARTES_MAX_TOKENS", 1200, minimum=1) == 1200


# Bug 4 — string-encoded Exa scores must be parsed, not zeroed.
def test_string_score_is_parsed(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa")
    payload = {"results": [{"title": "t", "url": "u", "score": "0.92", "highlights": ["h"]}]}
    install_fake_httpx(monkeypatch, lambda u, j, h: FakeResp(payload))
    res = run(ground("q"))
    assert res["confidence"] == 0.92 and res["status"] == "CONFIRMED"


# Bug 5 — a boolean in the score position must NOT read as 1.0 confidence.
def test_boolean_score_is_not_confidence():
    assert _coerce_score(True) == 0.0
    assert _coerce_score(False) == 0.0
    assert _coerce_score("0.5") == 0.5
    assert _coerce_score(0.7) == 0.7
    assert _coerce_score(None) == 0.0
    assert _coerce_score("garbage") == 0.0


# Bug 6 — compute_verdict must tolerate any input type without crashing.
def test_compute_verdict_tolerates_non_dict():
    for bad in ("a string", ["a", "list"], 42, 3.14, True):
        out = compute_verdict(bad)
        assert out == {"proceed": False, "blocking_questions": []}

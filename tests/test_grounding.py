"""Exa grounding: degrade safely, parse correctly, cache, never assert low-confidence."""
import asyncio

from helpers import FakeResp, install_fake_httpx

from descartes import grounding
from descartes.grounding import ground

run = asyncio.run


async def _noop(*a, **k):
    return None


def _exa_payload(scores):
    return {"results": [
        {"title": f"t{i}", "url": f"https://ex/{i}", "score": s,
         "highlights": [f"highlight {i}"], "text": f"body {i}"}
        for i, s in enumerate(scores)
    ]}


def test_no_key_degrades_to_unknown():
    res = run(ground("anything"))
    assert res["status"] == "UNKNOWN"
    assert res["confidence"] == 0.0
    assert res["facts"] == []
    assert "EXA_API_KEY" in res.get("note", "")


def test_empty_query_short_circuits():
    res = run(ground("   "))
    assert res["status"] == "UNKNOWN" and res["facts"] == []


def test_parses_facts_and_confidence_is_max_score(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa")
    install_fake_httpx(monkeypatch, lambda u, j, h: FakeResp(_exa_payload([0.3, 0.81, 0.5])))
    res = run(ground("what is x"))
    assert res["status"] == "CONFIRMED"
    assert res["confidence"] == 0.81           # max of the scores
    assert len(res["facts"]) == 3
    assert res["facts"][0]["claim"] == "highlight 0"
    assert res["facts"][0]["url"] == "https://ex/0"


def test_zero_scores_are_not_asserted(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa")
    install_fake_httpx(monkeypatch, lambda u, j, h: FakeResp(_exa_payload([0.0, 0.0])))
    res = run(ground("weak"))
    assert res["status"] == "UNKNOWN"          # facts exist but confidence 0 -> never asserted
    assert res["confidence"] == 0.0


def test_results_are_cached(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa")
    calls = {"n": 0}

    def handler(u, j, h):
        calls["n"] += 1
        return FakeResp(_exa_payload([0.7]))
    install_fake_httpx(monkeypatch, handler)
    a = run(ground("cache me"))
    b = run(ground("cache me"))
    assert a == b
    assert calls["n"] == 1                      # second call served from cache


def test_error_path_returns_unknown_with_error(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa")

    def boom(u, j, h):
        raise RuntimeError("exa exploded")
    install_fake_httpx(monkeypatch, boom)
    monkeypatch.setattr(grounding.asyncio, "sleep", _noop)
    res = run(ground("will fail"))
    assert res["status"] == "UNKNOWN"
    assert "exa exploded" in res.get("error", "")

"""Server surface: tool registration, verdict gating, reasoner resolution, e2e doubt()."""
import asyncio

from descartes.panel import ClaudeSamplingReasoner, OpenRouterReasoner, TemplateReasoner
from descartes.server import (
    _resolve_reasoner,
    compute_verdict,
    doubt,
    ground,
    mcp,
    verdict,
)

run = asyncio.run


# --------------------------------------------------------------------------- #
# tool registration
# --------------------------------------------------------------------------- #
def test_three_tools_registered():
    tools = run(mcp.list_tools())
    assert {t.name for t in tools} == {"doubt", "ground", "verdict"}


# --------------------------------------------------------------------------- #
# verdict gating
# --------------------------------------------------------------------------- #
def test_verdict_proceeds_when_converged_and_clean():
    out = compute_verdict({"converged": True, "needs_user": [], "doubt_log": [
        {"status": "CONFIRMED", "doubt": "a"}, {"status": "REFUTED", "doubt": "b"}]})
    assert out == {"proceed": True, "blocking_questions": []}


def test_verdict_blocks_on_open_doubt():
    out = compute_verdict({"converged": True, "needs_user": ["pick TTL"], "doubt_log": [
        {"status": "UNKNOWN", "doubt": "is X true?"}]})
    assert out["proceed"] is False
    assert "pick TTL" in out["blocking_questions"]
    assert "is X true?" in out["blocking_questions"]


def test_verdict_blocks_when_not_converged():
    out = compute_verdict({"converged": False, "needs_user": [], "doubt_log": []})
    assert out["proceed"] is False


def test_verdict_tool_matches_helper():
    payload = {"converged": True, "needs_user": [], "doubt_log": []}
    assert verdict(payload) == compute_verdict(payload)


def test_verdict_tolerates_garbage():
    assert compute_verdict({})["proceed"] is False
    assert compute_verdict(None)["proceed"] is False


# --------------------------------------------------------------------------- #
# reasoner resolution + sampling probe fallback
# --------------------------------------------------------------------------- #
class _Sess:
    def __init__(self, ok):
        self.ok = ok

    async def create_message(self, **kw):
        if not self.ok:
            raise RuntimeError("no sampling")

        class R:
            class content:
                text = "ok"
        return R


class _Ctx:
    def __init__(self, ok):
        self.session = _Sess(ok)


def test_resolve_reasoner_template_when_nothing():
    assert isinstance(run(_resolve_reasoner(None)), TemplateReasoner)


def test_resolve_reasoner_keeps_working_sampling():
    r = run(_resolve_reasoner(_Ctx(ok=True)))
    assert isinstance(r, ClaudeSamplingReasoner)


def test_resolve_reasoner_falls_back_to_template_when_sampling_dead():
    r = run(_resolve_reasoner(_Ctx(ok=False)))
    assert isinstance(r, TemplateReasoner)


def test_resolve_reasoner_falls_back_to_openrouter_when_sampling_dead(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    r = run(_resolve_reasoner(_Ctx(ok=False)))
    assert isinstance(r, OpenRouterReasoner)


# --------------------------------------------------------------------------- #
# end-to-end doubt() / ground() tools (no keys -> degrade paths)
# --------------------------------------------------------------------------- #
def test_doubt_tool_end_to_end_template():
    res = run(doubt("Cache the /report endpoint with Redis.",
                    context="api/report.py builds ReportModel; redis not a dependency yet.",
                    max_passes=20, ctx=None))
    assert set(["passes_used", "converged", "plan", "doubt_log", "needs_user"]).issubset(res)
    assert res["converged"] is True
    assert res["passes_used"] < 20


def test_ground_tool_degrades_without_key():
    res = run(ground("p99 latency budget"))
    assert res["status"] == "UNKNOWN" and res["confidence"] == 0.0

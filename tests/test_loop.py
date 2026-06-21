"""The core: convergence, termination, grounding discipline, panel agreement.

These tests lock down the invariants the whole product rests on:
  - the loop ALWAYS terminates and never exceeds the hard ceiling of 20,
  - it stops as soon as a pass produces no new load-bearing doubt (convergence),
  - it never asserts an answer without evidence (ungroundable -> NEEDS_HUMAN /
    low-confidence Exa -> UNKNOWN),
  - needs_user is exactly the NEEDS_HUMAN doubts,
  - panel disagreement escalates to the human.
"""
import asyncio

import pytest
from helpers import (
    DepthReasoner,
    PanelStub,
    ScriptedReasoner,
    strong_ground,
    stub_ground,
)

from descartes.loop import (
    HARD_CEILING,
    PRUNE_SYS,
    _clean_doubt,
    _keyword_hit,
    _norm,
    _template_doubts,
    _vote,
    extract_json,
    prune_doubts,
    run_doubt_loop,
)
from descartes.panel import TemplateReasoner

run = asyncio.run


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert extract_json('sure: [{"operator": "x"}] done')[0]["operator"] == "x"
    assert extract_json('{"status": "CONFIRMED"}')["status"] == "CONFIRMED"
    assert extract_json("not json at all") is None
    assert extract_json("") is None
    assert extract_json(None) is None


def test_norm_is_stable_and_bounded():
    assert _norm("  Hello   World  ") == "hello world"
    assert _norm("X" * 500) == "x" * 120  # clipped to 120


def test_clean_doubt_validates_and_defaults():
    assert _clean_doubt({"doubt": "ok?", "operator": "edge_case", "kind": "world"}) == {
        "operator": "edge_case", "doubt": "ok?", "kind": "world",
    }
    # unknown operator/kind default safely
    d = _clean_doubt({"doubt": "ok?", "operator": "bogus", "kind": "bogus"})
    assert d["operator"] == "assumption" and d["kind"] == "code"
    # empty / non-dict rejected
    assert _clean_doubt({"doubt": ""}) is None
    assert _clean_doubt("nope") is None


def test_vote_agreement_disagreement_and_empty():
    agree = _vote(['{"status":"CONFIRMED","resolution":"a"}', '{"status":"CONFIRMED","resolution":"b"}'])
    assert agree["status"] == "CONFIRMED"
    split = _vote(['{"status":"CONFIRMED","resolution":"yes"}', '{"status":"REFUTED","resolution":"no"}'])
    assert split["status"] == "NEEDS_HUMAN" and split["source"] == "panel-disagreement"
    assert _vote([])["status"] == "UNKNOWN"
    assert _vote(["garbage", "also garbage"])["status"] == "UNKNOWN"


def test_keyword_hit():
    assert _keyword_hit("does the rate limiter exist", "we have a rate limiter on /api") is True
    assert _keyword_hit("quantum flux capacitor", "a fastapi app") is False


# --------------------------------------------------------------------------- #
# template path: deterministic + convergent
# --------------------------------------------------------------------------- #
def test_template_doubts_are_deterministic_and_dedup_against_seen():
    plan = "# Plan\n- decision one\n- decision two"
    first = _template_doubts(plan, "ctx", set())
    again = _template_doubts(plan, "ctx", set())
    assert [d["doubt"] for d in first] == [d["doubt"] for d in again]  # deterministic
    seen = {_norm(d["doubt"]) for d in first}
    assert _template_doubts(plan, "ctx", seen) == []  # all already seen -> none new


def test_template_loop_converges_fast_and_under_ceiling():
    res = run(run_doubt_loop(
        "Add a rate limiter.", "api/server.py has a fastapi rate limiter helper",
        20, TemplateReasoner(), stub_ground))
    assert res["converged"] is True
    assert 1 < res["passes_used"] < HARD_CEILING
    assert res["engine"] == "template-fallback"
    assert set(["passes_used", "converged", "plan", "doubt_log", "needs_user"]).issubset(res)


# --------------------------------------------------------------------------- #
# termination + convergence (LLM stub of tunable depth)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("depth", [1, 2, 3, 5, 8])
def test_converges_in_depth_plus_one_passes(depth):
    res = run(run_doubt_loop("task", "evidence assumption verified context", 20,
                             DepthReasoner(depth=depth, doubt_kind="code"), stub_ground))
    assert res["converged"] is True
    assert res["passes_used"] == depth + 1
    assert res["passes_used"] <= HARD_CEILING
    assert len(res["doubt_log"]) == depth


def test_already_certain_plan_converges_in_one_pass():
    # depth 0 -> first pass yields no doubt at all -> converged immediately.
    res = run(run_doubt_loop("task", "evidence", 20,
                             DepthReasoner(depth=0, doubt_kind="code"), stub_ground))
    assert res["converged"] is True
    assert res["passes_used"] == 1
    assert res["doubt_log"] == []
    assert res["needs_user"] == []


def test_open_doubts_counts_unknown_and_needs_human():
    res = run(run_doubt_loop("task", "evidence", 20,
                             DepthReasoner(depth=3, doubt_kind="user"), stub_ground))
    assert res["open_doubts"] == 3  # all user doubts are NEEDS_HUMAN -> open
    assert res["open_doubts"] == len(res["needs_user"])


def test_hard_ceiling_caps_a_would_be_spiral():
    # depth far beyond the ceiling: must stop AT 20 and report not-converged.
    res = run(run_doubt_loop("task", "evidence", 20,
                             DepthReasoner(depth=999, doubt_kind="code"), stub_ground))
    assert res["passes_used"] == HARD_CEILING
    assert res["converged"] is False


def test_max_passes_is_clamped_to_20_even_if_caller_asks_for_more():
    res = run(run_doubt_loop("task", "evidence", 10_000,
                             DepthReasoner(depth=999, doubt_kind="code"), stub_ground))
    assert res["passes_used"] == HARD_CEILING


def test_max_passes_lower_bound_respected():
    res = run(run_doubt_loop("task", "evidence", 1,
                             DepthReasoner(depth=999, doubt_kind="code"), stub_ground))
    assert res["passes_used"] == 1
    assert res["converged"] is False


# --------------------------------------------------------------------------- #
# grounding discipline: never assert without evidence
# --------------------------------------------------------------------------- #
def test_world_doubt_low_confidence_stays_unknown_never_asserted():
    res = run(run_doubt_loop("task", "evidence", 20,
                             DepthReasoner(depth=1, doubt_kind="world"), stub_ground))
    statuses = [e["status"] for e in res["doubt_log"]]
    assert statuses == ["UNKNOWN"]            # low Exa confidence -> never CONFIRMED
    assert "CONFIRMED" not in statuses


def test_world_doubt_with_strong_evidence_resolves():
    res = run(run_doubt_loop("task", "evidence", 20,
                             DepthReasoner(depth=1, doubt_kind="world"), strong_ground(0.9)))
    assert res["doubt_log"][0]["status"] == "CONFIRMED"
    assert res["doubt_log"][0]["source"] == "https://example.com/x"


def test_code_doubt_without_context_is_needs_human():
    res = run(run_doubt_loop("task", "", 20,
                             DepthReasoner(depth=1, doubt_kind="code"), stub_ground))
    assert res["doubt_log"][0]["status"] == "NEEDS_HUMAN"


def test_needs_user_is_exactly_the_needs_human_doubts():
    res = run(run_doubt_loop("task", "evidence", 20,
                             DepthReasoner(depth=3, doubt_kind="user"), stub_ground))
    human = {e["doubt"] for e in res["doubt_log"] if e["status"] == "NEEDS_HUMAN"}
    assert set(res["needs_user"]) == human
    assert len(res["needs_user"]) == 3


# --------------------------------------------------------------------------- #
# panel agreement / disagreement through the whole loop
# --------------------------------------------------------------------------- #
def test_panel_disagreement_escalates_to_human():
    doubts = [{"operator": "assumption", "doubt": "is X true?", "kind": "code"}]
    reasoner = PanelStub(doubts, ['{"status":"CONFIRMED","resolution":"yes"}',
                                  '{"status":"REFUTED","resolution":"no"}',
                                  '{"status":"CONFIRMED","resolution":"yes"}'], panel_size=3)
    res = run(run_doubt_loop("task", "evidence", 20, reasoner, stub_ground))
    entry = res["doubt_log"][0]
    assert entry["status"] == "NEEDS_HUMAN"
    assert entry["source"] == "panel-disagreement"
    assert res["needs_user"] == ["is X true?"]


def test_panel_agreement_settles():
    doubts = [{"operator": "assumption", "doubt": "is Y true?", "kind": "code"}]
    reasoner = PanelStub(doubts, ['{"status":"CONFIRMED","resolution":"a"}',
                                  '{"status":"CONFIRMED","resolution":"b"}',
                                  '{"status":"CONFIRMED","resolution":"c"}'], panel_size=3)
    res = run(run_doubt_loop("task", "evidence", 20, reasoner, stub_ground))
    assert res["doubt_log"][0]["status"] == "CONFIRMED"
    assert res["needs_user"] == []
    assert res["converged"] is True


# --------------------------------------------------------------------------- #
# prune ("doubt the doubts")
# --------------------------------------------------------------------------- #
def test_prune_keeps_only_verbatim_survivors():
    doubts = [{"operator": "assumption", "doubt": "keep me?", "kind": "code"},
              {"operator": "edge_case", "doubt": "drop me?", "kind": "code"}]
    # prune returns only the first, verbatim
    keep_first = ScriptedReasoner({PRUNE_SYS: ['[{"operator":"assumption","doubt":"keep me?","kind":"code"}]']})
    survivors = run(prune_doubts(keep_first, doubts, "plan"))
    assert [d["doubt"] for d in survivors] == ["keep me?"]


def test_prune_reworded_doubts_are_dropped_not_trusted():
    doubts = [{"operator": "assumption", "doubt": "original wording", "kind": "code"}]
    reworded = ScriptedReasoner({PRUNE_SYS: ['[{"operator":"assumption","doubt":"totally new wording","kind":"code"}]']})
    survivors = run(prune_doubts(reworded, doubts, "plan"))
    assert survivors == []  # a reworded (=invented) doubt is not allowed through


def test_prune_parse_failure_preserves_doubts():
    doubts = [{"operator": "assumption", "doubt": "keep", "kind": "code"}]
    broken = ScriptedReasoner({PRUNE_SYS: ["not json"]})
    survivors = run(prune_doubts(broken, doubts, "plan"))
    assert survivors == doubts  # never silently lose doubts on a parse error

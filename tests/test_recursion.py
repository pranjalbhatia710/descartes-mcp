"""Recursive doubt + the accumulating knowledge base.

Descartes does not stop at a flat list of doubts: it questions its own answers,
recurses into the doubts that creates, and banks every grounded finding into a
knowledge base that feeds the next round and the final plan. These tests lock
that behaviour down — including that it is bounded (depth + node budget) and
that the no-model floor never recurses.
"""
import asyncio

from helpers import RecursiveReasoner, stub_ground

from descartes import loop
from descartes.loop import MAX_DEPTH, run_doubt_loop
from descartes.panel import TemplateReasoner

run = asyncio.run


def test_questions_the_answer_recursively_to_max_depth():
    res = run(run_doubt_loop("task", "evidence", 20, RecursiveReasoner(), stub_ground))
    assert res["converged"] is True
    depths = [e["depth"] for e in res["doubt_log"]]
    assert max(depths) == MAX_DEPTH                  # recursion stops at the depth ceiling
    assert res["max_depth_reached"] == MAX_DEPTH
    assert len(res["doubt_log"]) == MAX_DEPTH + 1    # root + one child per level
    # the log forms a single parent->child chain
    parents = {e["id"]: e["parent"] for e in res["doubt_log"]}
    roots = [i for i, p in parents.items() if p is None]
    assert len(roots) == 1


def test_doubt_tree_is_nested_and_linked():
    res = run(run_doubt_loop("task", "evidence", 20, RecursiveReasoner(), stub_ground))
    tree = res["doubt_tree"]
    assert len(tree) == 1
    root = tree[0]
    assert root["depth"] == 0 and root["parent"] is None
    # walk the chain down
    node, seen_depth = root, 0
    while node["children"]:
        child = node["children"][0]
        assert child["parent"] == node["id"]
        assert child["depth"] == seen_depth + 1
        node, seen_depth = child, child["depth"]
    assert seen_depth == MAX_DEPTH


def test_knowledge_base_accumulates_grounded_findings():
    res = run(run_doubt_loop("task", "evidence", 20, RecursiveReasoner(), stub_ground))
    kb = res["knowledge_base"]
    assert len(kb) == len(res["doubt_log"])          # every settled answer banks a fact
    assert all(k["verdict"] == "CONFIRMED" for k in kb)
    assert all({"claim", "evidence", "source", "depth"} <= set(k) for k in kb)


def test_node_budget_caps_runaway_recursion(monkeypatch):
    monkeypatch.setattr(loop, "MAX_DEPTH", 99)       # allow unbounded depth...
    monkeypatch.setattr(loop, "NODE_BUDGET", 5)      # ...but cap total doubts
    res = run(loop.run_doubt_loop("task", "evidence", 20, RecursiveReasoner(), stub_ground))
    assert len(res["doubt_log"]) <= 5
    assert res["converged"] is False                 # stopped by budget, not convergence
    assert "budget" in res["note"].lower()


def test_template_floor_never_recurses():
    res = run(run_doubt_loop("task", "evidence", 20, TemplateReasoner(), stub_ground))
    assert all(e["depth"] == 0 for e in res["doubt_log"])  # the no-model floor asks, never recurses
    assert res["max_depth_reached"] == 0

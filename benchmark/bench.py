"""Convergence benchmark for the Descartes doubt loop.

The reasoner is an *instant* stub of tunable "doubt depth", so wall-time measures
the loop machinery itself (not network latency) and the pass counts measure the
convergence behaviour we actually care about:

  - a plan with `depth` load-bearing doubts converges in exactly depth+1 passes,
  - pass count scales with depth but NEVER exceeds the hard ceiling of 20,
  - a 40-doubt "spiral" is capped at 20 and reported as not-converged.

Run it:   python -m benchmark.bench
It prints a markdown table + JSON and asserts every invariant (so CI can gate on
it). `run_all()` / `assert_invariants()` are importable for the test suite.
"""
import asyncio
import json
import time

from descartes.loop import (
    DRAFT_SYS,
    GEN_SYS,
    HARD_CEILING,
    PRUNE_SYS,
    RESOLVE_CODE_SYS,
    run_doubt_loop,
)
from descartes.panel import Reasoner, TemplateReasoner


async def _stub_ground(query):
    return {"query": query, "confidence": 0.0, "facts": [], "status": "UNKNOWN"}


class _BenchReasoner(Reasoner):
    """Instant LLM stub: emits exactly `depth` distinct code doubts, one new per
    pass, each resolvable to CONFIRMED. No network, no sleeps."""
    kind = "llm"
    name = "bench"

    def __init__(self, depth, panel_size=1):
        self.depth = depth
        self.panel_size = panel_size
        self._gen = 0

    async def complete(self, system, user, n=1):
        if system == DRAFT_SYS:
            return ["# Plan\n- decision a\n- decision b"]
        if system == GEN_SYS:
            self._gen += 1
            if self._gen <= self.depth:
                return [json.dumps([{"operator": "assumption",
                                     "doubt": f"is assumption {self._gen} verified?",
                                     "kind": "code"}])]
            return ["[]"]
        if system == PRUNE_SYS:
            after = user.split("CANDIDATE DOUBTS:\n", 1)
            return [after[1].split("\n\nReturn", 1)[0]] if len(after) > 1 else ["[]"]
        if system == RESOLVE_CODE_SYS:
            return ['{"status": "CONFIRMED", "resolution": "found", "citation": null}']
        return ["# Plan\n- decision a\n- decision b"]


# (label, doubt-depth)
SCENARIOS = [
    ("trivial", 0),
    ("simple", 1),
    ("moderate", 3),
    ("complex", 6),
    ("deep", 11),
    ("spiral-guard", 40),
]


async def _run_one(name, depth):
    reasoner = _BenchReasoner(depth)
    t0 = time.perf_counter()
    res = await run_doubt_loop(f"benchmark: {name}", "evidence context with keywords",
                               HARD_CEILING, reasoner, _stub_ground)
    ms = (time.perf_counter() - t0) * 1000
    return {
        "scenario": name, "depth": depth,
        "passes_used": res["passes_used"], "converged": res["converged"],
        "doubts": len(res["doubt_log"]), "needs_user": len(res["needs_user"]),
        "wall_ms": round(ms, 2),
    }


async def _run_template():
    t0 = time.perf_counter()
    res = await run_doubt_loop(
        "benchmark: template path",
        "api/server.py defines a fastapi app; a rate limiter helper is present",
        HARD_CEILING, TemplateReasoner(), _stub_ground)
    ms = (time.perf_counter() - t0) * 1000
    return {
        "scenario": "template(no-LLM)", "depth": "-",
        "passes_used": res["passes_used"], "converged": res["converged"],
        "doubts": len(res["doubt_log"]), "needs_user": len(res["needs_user"]),
        "wall_ms": round(ms, 2),
    }


async def run_all():
    rows = [await _run_one(name, depth) for name, depth in SCENARIOS]
    rows.append(await _run_template())
    return rows


def assert_invariants(rows):
    for r in rows:
        assert r["passes_used"] <= HARD_CEILING, f"exceeded hard ceiling: {r}"
        if isinstance(r["depth"], int) and r["depth"] < HARD_CEILING:
            assert r["converged"] is True, f"should converge: {r}"
            assert r["passes_used"] == r["depth"] + 1, f"depth+1 passes expected: {r}"
    spiral = next(r for r in rows if r["scenario"] == "spiral-guard")
    assert spiral["passes_used"] == HARD_CEILING and spiral["converged"] is False, spiral
    converging = sorted((r for r in rows if isinstance(r["depth"], int) and r["depth"] < HARD_CEILING),
                        key=lambda r: r["depth"])
    passes = [r["passes_used"] for r in converging]
    assert passes == sorted(passes), f"passes must increase with depth: {passes}"


def _table(rows):
    head = "| scenario | depth | passes | converged | doubts | needs_user | wall_ms |"
    sep = "|---|---|---|---|---|---|---|"
    body = "\n".join(
        "| {scenario} | {depth} | {passes_used} | {converged} | {doubts} | {needs_user} | {wall_ms} |".format(**r)
        for r in rows
    )
    return "\n".join([head, sep, body])


def main():
    rows = asyncio.run(run_all())
    assert_invariants(rows)
    print("# Descartes convergence benchmark\n")
    print(_table(rows))
    print(f"\nAll invariants hold: each plan converges in depth+1 passes, never exceeds the hard "
          f"ceiling of {HARD_CEILING}, and a 40-doubt spiral is capped at {HARD_CEILING} "
          f"(converged=False).")
    print("\nJSON: " + json.dumps(rows))


if __name__ == "__main__":
    main()

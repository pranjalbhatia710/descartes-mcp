"""The benchmark is part of CI: its convergence invariants must hold."""
import asyncio

from benchmark.bench import assert_invariants, run_all
from descartes.loop import HARD_CEILING


def test_benchmark_invariants_hold():
    rows = asyncio.run(run_all())
    assert_invariants(rows)  # raises if any invariant is violated
    # spot-check the headline numbers
    by_name = {r["scenario"]: r for r in rows}
    assert by_name["trivial"]["passes_used"] == 1
    assert by_name["deep"]["passes_used"] == 12
    assert by_name["spiral-guard"]["passes_used"] == HARD_CEILING
    assert by_name["spiral-guard"]["converged"] is False
    assert by_name["template(no-LLM)"]["converged"] is True


def test_benchmark_is_fast():
    rows = asyncio.run(run_all())
    # pure loop machinery (instant reasoner) should be milliseconds per scenario
    assert all(r["wall_ms"] < 500 for r in rows), [r["wall_ms"] for r in rows]

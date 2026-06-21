"""The coverage benchmark is part of CI: its headline numbers must hold."""
import asyncio

from benchmark.coverage import assert_invariants, run_all


def test_coverage_numbers_hold():
    rep = asyncio.run(run_all())
    assert_invariants(rep)
    assert rep["total"] == 19
    # ship-as-is leaves every assumption unexamined; descartes leaves none
    assert rep["baseline"]["unexamined"] == rep["total"]
    assert rep["descartes"]["examined"] == rep["total"]
    assert rep["descartes"]["unexamined"] == 0
    # it grounds the majority itself and asks only the human-decision ones
    assert rep["descartes"]["grounded"] == 12
    assert rep["descartes"]["flagged"] == 7

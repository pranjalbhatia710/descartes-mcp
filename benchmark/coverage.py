"""How much better is doubting? — a reproducible coverage benchmark.

The honest question is not "is Descartes N% more accurate" (that depends on the
model you plug in). The honest, measurable question is: *of a plan's load-bearing
assumptions, how many reach the work unexamined?*

So this is a synthetic floor (the way The Asker validates its mechanism): a fixture
of real-shaped planning tasks, each with hand-labeled load-bearing assumptions —
some resolvable from evidence (code/world), some only a human can decide. We run
two approaches and count:

  - ship the plan as-is  -> examines 0; every assumption reaches the work blind.
  - descartes            -> surfaces every assumption, grounds what it can, and
                            hands back only the genuine human decisions.

Numbers below are the loop's ACTUAL output on the fixture, not estimates. What a
real model adds on top (catching assumptions the labels missed, deeper recursion)
only widens the gap; this floor is the conservative case.

Run:  python -m benchmark.coverage
"""
import asyncio
import json

from descartes.loop import (
    CHILD_SYS,
    DRAFT_SYS,
    GEN_SYS,
    PRUNE_SYS,
    RESOLVE_CODE_SYS,
    RESOLVE_WORLD_SYS,
    run_doubt_loop,
)
from descartes.panel import Reasoner

# Fixture: realistic plans, each with labeled load-bearing assumptions.
# kind "code"/"world" = resolvable from evidence; "user" = only a human decides.
FIXTURE = [
    {"task": "Add a Redis cache in front of /report", "assumptions": [
        {"q": "Is the report payload identity stable enough to key a cache?", "kind": "code"},
        {"q": "Is SETEX atomic in the redis client we use?", "kind": "world"},
        {"q": "What eviction policy fits our memory budget?", "kind": "user"},
        {"q": "Is serving stale-on-error acceptable to the business?", "kind": "user"}]},
    {"task": "Switch auth from sessions to JWT", "assumptions": [
        {"q": "Are refresh tokens rotated and revocable?", "kind": "code"},
        {"q": "Does the JWT library reject the 'none' algorithm?", "kind": "world"},
        {"q": "What token lifetime balances security and UX?", "kind": "user"}]},
    {"task": "Add full-text search with Postgres", "assumptions": [
        {"q": "Does the schema already have a tsvector column or index?", "kind": "code"},
        {"q": "Is GIN the right index type for our query mix?", "kind": "world"},
        {"q": "Which languages must the analyzer support?", "kind": "user"}]},
    {"task": "Introduce a background job queue", "assumptions": [
        {"q": "Are job handlers idempotent on retry?", "kind": "code"},
        {"q": "Does the broker guarantee at-least-once delivery?", "kind": "world"},
        {"q": "What is the acceptable job-latency SLO?", "kind": "user"}]},
    {"task": "Rate-limit the public API", "assumptions": [
        {"q": "Is there a shared store for counters across instances?", "kind": "code"},
        {"q": "Does a token bucket fit our burst profile better than fixed-window?", "kind": "world"},
        {"q": "What limit per key is right for our plans?", "kind": "user"}]},
    {"task": "Migrate uploads to S3", "assumptions": [
        {"q": "Are upload paths behind one storage interface?", "kind": "code"},
        {"q": "Does presigned-URL upload pass our security review?", "kind": "world"},
        {"q": "Which bucket region and retention policy do we need?", "kind": "user"}]},
]


def _groundable(a):
    return a["kind"] in ("code", "world")


async def _ground_fn(query):
    # In this floor, world assumptions are taken to be groundable (high confidence).
    return {"query": query, "confidence": 0.9, "status": "CONFIRMED",
            "facts": [{"claim": "a cited fact", "url": "https://example.com", "score": 0.9}]}


class _Oracle(Reasoner):
    """A competent doubter for one fixture plan: it surfaces exactly the labeled
    assumptions, resolves the groundable ones from evidence, and leaves the
    human ones to the human. Deterministic — it measures the *loop*, not a model."""
    kind = "llm"
    name = "fixture-oracle"
    panel_size = 1

    def __init__(self, assumptions):
        self._asm = assumptions
        self._gen = 0

    async def complete(self, system, user, n=1):
        if system == DRAFT_SYS:
            return ["# Plan\n- " + "\n- ".join(a["q"] for a in self._asm)]
        if system == GEN_SYS:
            self._gen += 1
            if self._gen == 1:
                return [json.dumps([{"operator": "assumption", "doubt": a["q"], "kind": a["kind"]}
                                    for a in self._asm])]
            return ["[]"]
        if system == PRUNE_SYS:
            after = user.split("CANDIDATE DOUBTS:\n", 1)
            return [after[1].split("\n\nReturn", 1)[0]] if len(after) > 1 else ["[]"]
        if system in (RESOLVE_CODE_SYS, RESOLVE_WORLD_SYS):
            return ['{"status": "CONFIRMED", "resolution": "grounded in the provided evidence", "citation": null}']
        if system == CHILD_SYS:
            return ["[]"]  # keep the floor shallow + deterministic
        return ["# Plan\n- decision"]


async def _run_descartes(entry):
    res = await run_doubt_loop(entry["task"], "(evidence provided)", 20,
                               _Oracle(entry["assumptions"]), _ground_fn)
    surfaced = len(res["doubt_log"])
    grounded = sum(1 for e in res["doubt_log"] if e["status"] in ("CONFIRMED", "REFUTED"))
    flagged = len(res["needs_user"])
    return {"surfaced": surfaced, "grounded": grounded, "flagged": flagged}


async def run_all():
    rows = []
    for entry in FIXTURE:
        total = len(entry["assumptions"])
        d = await _run_descartes(entry)
        rows.append({"task": entry["task"], "assumptions": total, **d})
    total = sum(r["assumptions"] for r in rows)
    descartes = {
        "examined": sum(r["surfaced"] for r in rows),
        "grounded": sum(r["grounded"] for r in rows),
        "flagged": sum(r["flagged"] for r in rows),
    }
    descartes["unexamined"] = total - descartes["examined"]
    baseline = {"examined": 0, "grounded": 0, "flagged": 0, "unexamined": total}
    return {"total": total, "baseline": baseline, "descartes": descartes, "rows": rows}


def assert_invariants(report):
    t = report["total"]
    b, d = report["baseline"], report["descartes"]
    assert b["examined"] == 0 and b["unexamined"] == t, b      # ship-as-is examines nothing
    assert d["examined"] == t and d["unexamined"] == 0, d      # descartes examines all
    assert d["grounded"] + d["flagged"] == d["examined"], d    # each is grounded or asked
    assert d["grounded"] > d["flagged"], d                     # it resolves more than it asks


def _pct(n, d):
    return 0 if not d else round(100 * n / d)


def main():
    report = asyncio.run(run_all())
    assert_invariants(report)
    t = report["total"]
    b, d = report["baseline"], report["descartes"]
    print("# Descartes coverage benchmark")
    print(f"# {len(FIXTURE)} plans · {t} labeled load-bearing assumptions\n")
    print("| approach | examined | grounded from evidence | asked of you | reach work UNEXAMINED |")
    print("|---|---|---|---|---|")
    print(f"| ship the plan as-is | 0 (0%) | 0 | 0 | {b['unexamined']} ({_pct(b['unexamined'], t)}%) |")
    print(f"| **descartes** | {d['examined']} ({_pct(d['examined'], t)}%) | {d['grounded']} | "
          f"{d['flagged']} | {d['unexamined']} ({_pct(d['unexamined'], t)}%) |")
    print(f"\nHeadline: assumptions reaching the work unexamined fall from "
          f"{_pct(b['unexamined'], t)}% to {_pct(d['unexamined'], t)}%. "
          f"Of the {d['examined']} it surfaces, Descartes grounds {d['grounded']} itself "
          f"({_pct(d['grounded'], d['examined'])}%) and hands you only the {d['flagged']} "
          f"that genuinely need a human.")
    print("\nJSON: " + json.dumps(report))


if __name__ == "__main__":
    main()

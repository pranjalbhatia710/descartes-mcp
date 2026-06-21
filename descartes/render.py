"""Terminal visualization for Descartes — watch it doubt, live.

Two visual surfaces, both protocol-safe:
  - `descartes --demo` runs a scripted doubt session and renders it here with
    colour, a spinner, a self-drawing doubt tree, and the knowledge base filling.
    This module owns stdout (it is a CLI path, never the stdio server).
  - The live MCP `doubt()` tool reports the SAME events to the client as
    progress + log notifications (see server._make_on_event) — that is how the
    tool shows it is working inside Claude Code.

Animation is gated on a real TTY, so piping or CI runs instantly with no escapes.
"""
import json
import os
import re
import sys
import time

from .loop import (
    CHILD_SYS,
    DRAFT_SYS,
    GEN_SYS,
    PRUNE_SYS,
    RESOLVE_CODE_SYS,
    RESOLVE_WORLD_SYS,
    _clip,
    run_doubt_loop,
)
from .panel import Reasoner

_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _wrap(code):
    def f(s):
        return f"\x1b[{code}m{s}\x1b[0m" if _TTY else str(s)
    return f


BOLD = _wrap("1")
DIM = _wrap("2")
GREEN = _wrap("32")
RED = _wrap("31")
YELLOW = _wrap("33")
MAGENTA = _wrap("35")
CYAN = _wrap("36")
GOLD = _wrap("38;5;179")

_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_GLYPH = {
    "CONFIRMED": (GREEN, "✓"), "REFUTED": (RED, "✗"),
    "UNKNOWN": (YELLOW, "?"), "NEEDS_HUMAN": (MAGENTA, "⚑"),
}


def _key(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _after(user, marker):
    return user.split(marker, 1)[1].split("\n\n", 1)[0].strip() if marker in user else ""


# --------------------------------------------------------------------------- #
# the renderer (consumes loop events)
# --------------------------------------------------------------------------- #
class Renderer:
    def __init__(self, animate=None):
        self.animate = _TTY if animate is None else animate

    async def handle(self, evt):
        t = evt.get("type")
        if t == "start":
            print(GOLD(BOLD(f"  ▸ doubting: {evt['prompt']}")))
            print(DIM(f"    engine: {evt['engine']} · ceiling {evt['cap']} passes\n"))
        elif t == "draft":
            print(DIM("    drafted a plan. now doubting every line of it…\n"))
        elif t == "pass":
            print(BOLD(CYAN(f"  ── pass {evt['pass']} ") + DIM("─" * 38)))
        elif t == "thinking" and self.animate:
            ind = "    " + "  " * evt["depth"]
            for i in range(10):
                sys.stdout.write(f"\r{ind}{CYAN(_SPIN[i % len(_SPIN)])} "
                                 f"{DIM('doubting: ' + _clip(evt['doubt'], 56))}")
                sys.stdout.flush()
                time.sleep(0.045)
        elif t == "doubt":
            col, glyph = _GLYPH.get(evt["status"], (DIM, "·"))
            ind = "    " + "  " * evt["depth"]
            branch = "◆" if evt["depth"] == 0 else "↳"
            if self.animate:
                sys.stdout.write("\r\x1b[2K")
            print(f"{ind}{DIM(branch)} {col(glyph)} {col(evt['status'])} "
                  f"{DIM('[' + evt['operator'] + ']')} {evt['doubt']}")
            if evt.get("resolution"):
                print(f"{ind}      {DIM(_clip(evt['resolution'], 74))}")
        elif t == "done":
            verdict = GREEN("✓ CONVERGED") if evt["converged"] else YELLOW("◦ stopped at the ceiling")
            tail = DIM(f"in {evt['passes']} pass(es) · max depth {evt['max_depth']}")
            print()
            print(f"  {BOLD(verdict)}  {tail}")
            print("  " + DIM(f"{evt['doubts']} doubts · ") + GREEN(f"{evt['grounded']} grounded")
                  + DIM(" · ") + MAGENTA(f"{evt['needs_user']} for you"))

    def summary(self, res):
        kb = res["knowledge_base"]
        print(BOLD("\n  knowledge base ") + DIM(f"({len(kb)} grounded facts, reused not re-derived)"))
        for k in kb:
            print(f"    {GREEN('•')} {DIM('[' + k['verdict'] + ']')} {_clip(k['claim'], 68)}")
        if res["needs_user"]:
            print(BOLD(MAGENTA("\n  needs you ")) + DIM("(the few only you can decide)"))
            for q in res["needs_user"]:
                print(f"    {MAGENTA('⚑')} {q}")
        print(BOLD("\n  refined plan"))
        for line in res["plan"].splitlines():
            print("    " + (line if line.strip() else ""))
        print()


# --------------------------------------------------------------------------- #
# the scripted demo session (no keys, no network)
# --------------------------------------------------------------------------- #
DEMO_PROMPT = "Add a Redis cache in front of the /report endpoint"
DEMO_CONTEXT = ("api/report.py builds ReportModel and returns it; models.py has report_version "
                "(bumped on re-analysis); redis is not yet a dependency.")
_DEMO_PLAN = ("# Plan\n- Cache key = hash(campaign_id + report_version)\n"
              "- Cache-aside in api/report.py; miss -> compute -> SETEX\n"
              "- TTL 24h; fail-open on Redis error")

_TOP = [
    {"operator": "assumption", "kind": "code",
     "doubt": "Is the report payload identity stable enough to key a cache?",
     "status": "CONFIRMED", "resolution": "models.py bumps report_version on re-analysis, so the key is stable.",
     "child": {"operator": "edge_case", "kind": "code",
               "doubt": "Is that cache key collision-free across campaigns?",
               "status": "CONFIRMED", "resolution": "key = sha256(campaign_id + report_version)."}},
    {"operator": "inversion", "kind": "code",
     "doubt": "If Redis is down, should the endpoint fail rather than recompute?",
     "status": "REFUTED", "resolution": "A recompute path already exists; fail-open is the safe default."},
    {"operator": "named_source", "kind": "world",
     "doubt": "Is SETEX atomic in the redis client we use?",
     "status": "CONFIRMED", "resolution": "SETEX is atomic per the Redis docs.",
     "child": {"operator": "second_order", "kind": "world",
               "doubt": "Is it still atomic under the retrying client wrapper?",
               "status": "CONFIRMED", "resolution": "redis-py SETEX is idempotent on retry."}},
    {"operator": "reversibility", "kind": "user",
     "doubt": "Is serving a stale-on-error cached report acceptable to the business?",
     "status": "NEEDS_HUMAN", "resolution": ""},
    {"operator": "second_order", "kind": "user",
     "doubt": "Which eviction policy fits the memory budget, allkeys-lru or volatile-ttl?",
     "status": "NEEDS_HUMAN", "resolution": ""},
]

_REGISTRY, _CHILDREN = {}, {}
for _d in _TOP:
    _REGISTRY[_key(_d["doubt"])] = {"status": _d["status"], "resolution": _d["resolution"]}
    if _d.get("child"):
        _CHILDREN[_key(_d["doubt"])] = _d["child"]
        _REGISTRY[_key(_d["child"]["doubt"])] = {
            "status": _d["child"]["status"], "resolution": _d["child"]["resolution"]}


class DemoReasoner(Reasoner):
    """Scripted, deterministic doubting session for the terminal demo."""
    kind = "llm"
    name = "demo"
    panel_size = 1

    def __init__(self):
        self._gen = 0
        self._emitted = set()

    async def complete(self, system, user, n=1):
        if system == DRAFT_SYS:
            return [_DEMO_PLAN]
        if system == GEN_SYS:
            self._gen += 1
            if self._gen == 1:
                return [json.dumps([{"operator": d["operator"], "doubt": d["doubt"], "kind": d["kind"]}
                                    for d in _TOP])]
            return ["[]"]
        if system == PRUNE_SYS:
            after = user.split("CANDIDATE DOUBTS:\n", 1)
            return [after[1].split("\n\nReturn", 1)[0]] if len(after) > 1 else ["[]"]
        if system in (RESOLVE_CODE_SYS, RESOLVE_WORLD_SYS):
            spec = _REGISTRY.get(_key(_after(user, "DOUBT:\n")),
                                 {"status": "CONFIRMED", "resolution": "grounded in evidence"})
            return [json.dumps({"status": spec["status"], "resolution": spec["resolution"], "citation": None})]
        if system == CHILD_SYS:
            k = _key(_after(user, "ORIGINAL DOUBT:\n"))
            child = _CHILDREN.get(k)
            if child and k not in self._emitted:
                self._emitted.add(k)
                return [json.dumps([{"operator": child["operator"], "doubt": child["doubt"],
                                     "kind": child["kind"]}])]
            return ["[]"]
        return [_DEMO_PLAN]


async def _demo_ground(query):
    return {"query": query, "confidence": 0.9, "status": "CONFIRMED",
            "facts": [{"claim": "per the documentation", "url": "https://redis.io", "score": 0.9}]}


def run_demo():
    import asyncio
    rule = "  " + DIM("·" * 56)
    print()
    print("  " + GOLD(BOLD("D E S C A R T E S")) + DIM("   doubt, until what remains is certain."))
    print(rule)
    renderer = Renderer()
    res = asyncio.run(run_doubt_loop(DEMO_PROMPT, DEMO_CONTEXT, 20,
                                     DemoReasoner(), _demo_ground, on_event=renderer.handle))
    print(rule)
    renderer.summary(res)
    return res

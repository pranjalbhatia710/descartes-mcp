"""Descartes MCP server — doubt, until what remains is certain.

Tools:
  doubt(prompt, context="", max_passes=20)  -> run the convergence loop
  ground(query)                             -> cited, confidence-scored Exa facts
  verdict(result)                           -> proceed / blocking questions gate

Keys are read from the environment only (FIREWORKS_API_KEY, EXA_API_KEY,
OPENROUTER_API_KEY). Missing keys degrade the run; they are never stored or
logged.
"""
import os

from mcp.server.fastmcp import Context, FastMCP

from .grounding import ground as exa_ground
from .loop import run_doubt_loop
from .panel import (
    ClaudeSamplingReasoner,
    OpenRouterReasoner,
    TemplateReasoner,
    select_reasoner,
)

mcp = FastMCP("descartes")


async def _resolve_reasoner(ctx):
    """Pick a backend and, for the sampling path, verify the client actually
    supports sampling — otherwise degrade (OpenRouter -> template)."""
    reasoner = select_reasoner(ctx)
    if isinstance(reasoner, ClaudeSamplingReasoner):
        probe = await reasoner.complete("Reply with the single word: ok.", "ok")
        if not probe:
            if os.environ.get("OPENROUTER_API_KEY"):
                return OpenRouterReasoner(os.environ["OPENROUTER_API_KEY"])
            return TemplateReasoner()
    return reasoner


@mcp.tool()
async def doubt(prompt: str, context: str = "", max_passes: int = 20, ctx: Context = None) -> dict:
    """Doubt every decision in a plan, doubt the doubts, and answer each from
    real evidence — codebase `context` for code doubts, Exa for world doubts —
    or flag it for the human. Iterate until the plan produces no new
    load-bearing doubt (convergence). 20 is a hard ceiling, never a target.

    Args:
        prompt: the task / plan to harden.
        context: real evidence (paste relevant files, types, tests, notes) used
            to resolve code doubts without guessing.
        max_passes: convergence ceiling (clamped to 20).

    Returns:
        {passes_used, converged, plan, doubt_log, needs_user, engine, open_doubts}
    """
    reasoner = await _resolve_reasoner(ctx)
    return await run_doubt_loop(prompt, context, max_passes, reasoner, exa_ground)


@mcp.tool()
async def ground(query: str) -> dict:
    """Exa deep search -> cited, confidence-scored facts (used inside the loop).
    Low confidence is reported as UNKNOWN and never asserted."""
    return await exa_ground(query)


@mcp.tool()
def verdict(result: dict) -> dict:
    """Gate a doubt() result. Proceed only if the plan converged with no open
    load-bearing doubt remaining; otherwise return the blocking questions."""
    return compute_verdict(result)


def compute_verdict(result: dict) -> dict:
    if not isinstance(result, dict):
        result = {}
    needs_user = list(result.get("needs_user") or [])
    converged = bool(result.get("converged"))

    blocking = list(needs_user)
    for entry in result.get("doubt_log", []) or []:
        if entry.get("status") in ("UNKNOWN", "NEEDS_HUMAN"):
            q = entry.get("doubt")
            if q and q not in blocking:
                blocking.append(q)

    return {"proceed": converged and not blocking, "blocking_questions": blocking}


# A little whimsy for the human-facing CLI paths only. NEVER printed on the
# stdio-server path (that channel is the MCP protocol and must stay clean).
_EPIGRAPHS = (
    "I think, therefore I doubt.",
    "Give me evidence, or give me NEEDS_HUMAN.",
    "I doubted this very sentence. It survived.",
    "The only thing I take on faith is your API key.",
    "Twenty passes is a ceiling, never a dare.",
    "An assumption unexamined is not worth shipping.",
    "Dubito, ergo cogito, ergo commit.",
)


def _banner():
    width = 45
    rule = "  +" + "-" * width + "+"
    lines = ("D E S C A R T E S", "doubt, until what remains is certain.")
    body = "\n".join("  |" + line.center(width) + "|" for line in lines)
    return f"{rule}\n{body}\n{rule}"


def _an_epigraph():
    import random
    return random.choice(_EPIGRAPHS)


def _print_epigraph():
    print(_banner())
    print(f'\n  ✒  "{_an_epigraph()}"\n')


def _selftest():
    """Prove the loop converges early and never spirals — no keys needed."""
    import asyncio
    import json

    async def _stub_ground(query):
        return {"query": query, "confidence": 0.0, "facts": [], "status": "UNKNOWN"}

    print(_banner())
    print("\n  Descartes dips the quill and resolves to doubt everything...\n")

    res = asyncio.run(run_doubt_loop(
        prompt="Add a rate limiter to the public API.",
        context="api/server.py defines a FastAPI app on port 8000; no limiter is present today.",
        max_passes=20,
        reasoner=TemplateReasoner(),
        ground_fn=_stub_ground,
    ))
    summary = {k: res[k] for k in ("passes_used", "converged", "engine", "open_doubts")}
    summary["needs_user_count"] = len(res["needs_user"])
    print(json.dumps(summary, indent=2))
    print(f"[selftest] converged={res['converged']} in {res['passes_used']} pass(es) "
          f"(hard ceiling 20) via engine={res['engine']}")
    print(f"  note: {res['note']}")
    print(verdict(res))
    print(f'\n  ✒  "{_an_epigraph()}"')


def main():
    import sys
    if "--selftest" in sys.argv:
        _selftest()
        return
    if "--epigraph" in sys.argv or "--about" in sys.argv:
        _print_epigraph()
        return
    if "--version" in sys.argv:
        from . import __version__
        print(f"descartes-mcp {__version__}")
        return
    mcp.run()


if __name__ == "__main__":
    main()

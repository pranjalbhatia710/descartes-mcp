"""The Descartes doubt loop.

    draft -> doubt -> doubt-the-doubts -> answer-from-evidence -> revise -> repeat

Stopping rule (the important part): the loop stops the moment a full pass
produces NO new load-bearing doubt — that is convergence, and it may happen on
pass 2 or pass 9. 20 is a HARD CEILING, never a target. We track convergence by
counting *new* (previously-unseen) load-bearing doubts each pass; 0 new -> done.

Grounding rule: every answer resolves against real evidence (codebase `context`
or Exa) or becomes a question for the user (NEEDS_HUMAN). Nothing is asserted by
guessing; low-confidence Exa stays UNKNOWN.
"""
import json
import os
import re

from .operators import OPERATOR_IDS, OPERATOR_TEXT, OPERATORS

VALID_STATUSES = {"CONFIRMED", "REFUTED", "UNKNOWN", "NEEDS_HUMAN"}
OPEN_STATUSES = {"UNKNOWN", "NEEDS_HUMAN"}


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


EXA_CONFIDENCE_FLOOR = _env_float("DESCARTES_EXA_FLOOR", 0.45)
HARD_CEILING = 20

# One honest line per engine so the caller always knows how the run was powered
# (and, on the no-key floor, that the doubts were handed back rather than guessed).
ENGINE_NOTES = {
    "fireworks-panel": "Doubted across a panel of Fireworks model families; where they "
                       "disagreed, the doubt was escalated to you.",
    "claude-sampling": "Doubted with your own Claude model via MCP sampling — no external "
                       "API keys required.",
    "openrouter": "Doubted with a single model via OpenRouter.",
    "template-fallback": "No API keys and no MCP sampling were available, so I did not guess: "
                         "I surfaced the load-bearing doubts as questions for you to answer. "
                         "For automatic doubting, run inside an MCP client that supports "
                         "sampling (Claude alone, no keys), or set FIREWORKS_API_KEY / "
                         "OPENROUTER_API_KEY.",
}

_STOP = {"this", "that", "with", "from", "what", "which", "does", "will", "have",
         "here", "there", "would", "could", "should", "about", "into", "than",
         "then", "they", "them", "your", "plan", "step", "doubt"}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    # truncated key for *fuzzy* dedup (catches near-duplicate rewordings)
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:120]


def _fullkey(text: str) -> str:
    # untruncated key for the *verbatim* prune check (no prefix collisions)
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_json(text):
    """Tolerant JSON extraction from a model completion (handles code fences)."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    for opn, cls in (("[", "]"), ("{", "}")):
        i, j = t.find(opn), t.rfind(cls)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                continue
    return None


def _res(status, resolution, source):
    return {"status": status, "resolution": resolution, "source": source}


def _clean_doubt(d):
    if not isinstance(d, dict):
        return None
    text = str(d.get("doubt", "")).strip()
    if not text:
        return None
    op = str(d.get("operator", "assumption")).strip().lower()
    if op not in OPERATOR_IDS:
        op = "assumption"
    kind = str(d.get("kind", "code")).strip().lower()
    if kind not in ("code", "world", "user"):
        kind = "code"
    return {"operator": op, "doubt": text, "kind": kind}


def _clip(text, limit):
    text = text or ""
    return (text[:limit] + "…") if len(text) > limit else text


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #
DRAFT_SYS = (
    "You are Descartes, a planning engine whose method is radical doubt. Draft a "
    "concrete, minimal plan: the specific decisions and steps the work requires. "
    "Prefer the smallest plan that actually does the job."
)

GEN_SYS = (
    "You apply Cartesian doubt to a plan. A LOAD-BEARING doubt is one where, if the "
    "assumption turns out wrong, the plan MUST change. Use these doubt operators:\n"
    f"{OPERATOR_TEXT}\n"
    "Rules: (1) Raise only NEW load-bearing doubts not already in the resolved list. "
    "(2) Do NOT manufacture doubt about trivial or already-settled things — if nothing "
    "new and load-bearing remains, return an empty array []. (3) Tag each doubt's kind: "
    "'code' if answerable from the provided codebase context, 'world' if it needs an "
    "external fact, 'user' if only the human can decide (preference, scope, priorities). "
    'Output ONLY a JSON array of objects: '
    '{"operator": <id>, "doubt": <one sharp question>, "kind": "code"|"world"|"user"}.'
)

PRUNE_SYS = (
    "You are the skeptic's skeptic. Given candidate doubts about a plan, remove any that "
    "are trivial, manufactured, redundant, or NOT load-bearing (the plan would not change "
    "regardless of the answer). Keep only the essential few. Copy the surviving objects "
    "VERBATIM — do not reword them. Output ONLY the surviving JSON array (same schema)."
)

RESOLVE_CODE_SYS = (
    "Resolve a doubt using ONLY the provided codebase evidence. If the evidence does not "
    "contain the answer, you MUST respond UNKNOWN (or NEEDS_HUMAN if it is a human decision). "
    "Never guess; never use outside knowledge as fact. Output ONLY JSON: "
    '{"status": "CONFIRMED"|"REFUTED"|"UNKNOWN"|"NEEDS_HUMAN", "resolution": <one line>, '
    '"citation": <where in the evidence, or null>}.'
)

RESOLVE_WORLD_SYS = (
    "Resolve a doubt using ONLY the provided cited facts. If the facts are weak, off-topic, "
    "or low-confidence, respond UNKNOWN — do not assert. Output ONLY JSON: "
    '{"status": "CONFIRMED"|"REFUTED"|"UNKNOWN"|"NEEDS_HUMAN", "resolution": <one line>, '
    '"citation": <url>}.'
)

REVISE_SYS = (
    "Revise the plan to incorporate what the doubts established: fold in confirmed facts, "
    "change or drop anything refuted, and mark anything still unresolved as '[OPEN: …]'. "
    "Keep it concrete and minimal. Output ONLY the full revised plan in markdown."
)


# --------------------------------------------------------------------------- #
# steps — each branches once for the deterministic template path
# --------------------------------------------------------------------------- #
async def draft_plan(reasoner, prompt, context):
    if reasoner.kind == "template":
        return _template_plan(prompt, context)
    user = (
        f"TASK:\n{prompt}\n\n"
        f"CONTEXT (real evidence — code, types, tests, notes):\n{context or '(none provided)'}\n\n"
        "Write the plan as a short markdown list of the key decisions/steps. No preamble."
    )
    out = await reasoner.complete(DRAFT_SYS, user, n=1)
    if out and out[0].strip():
        return out[0].strip()
    return _template_plan(prompt, context)


async def generate_doubts(reasoner, plan, prompt, context, seen_texts):
    if reasoner.kind == "template":
        return _template_doubts(plan, context, seen_texts)
    already = "\n".join(f"- {t}" for t in list(seen_texts)[-40:]) or "(none yet)"
    user = (
        f"TASK:\n{prompt}\n\nCURRENT PLAN:\n{plan}\n\n"
        f"CODEBASE CONTEXT AVAILABLE:\n{_clip(context, 4000) or '(none)'}\n\n"
        f"DOUBTS ALREADY RAISED/RESOLVED:\n{already}\n\n"
        "List the new load-bearing doubts now (or [] if none remain)."
    )
    outs = await reasoner.complete(GEN_SYS, user, n=reasoner.panel_size)
    collected = []
    for text in outs:
        parsed = extract_json(text)
        if isinstance(parsed, list):
            for raw in parsed:
                d = _clean_doubt(raw)
                if d:
                    collected.append(d)
    # union across panel members, dedup by normalized text
    uniq = {}
    for d in collected:
        uniq.setdefault(_norm(d["doubt"]), d)
    return list(uniq.values())


async def prune_doubts(reasoner, doubts, plan):
    """Doubt the doubts: drop the non-load-bearing ones."""
    if not doubts or reasoner.kind == "template":
        return doubts  # template doubts are already minimal and deterministic
    user = (
        f"PLAN:\n{plan}\n\nCANDIDATE DOUBTS:\n{json.dumps(doubts, indent=2)}\n\n"
        "Return the surviving load-bearing doubts as a JSON array (verbatim)."
    )
    out = await reasoner.complete(PRUNE_SYS, user, n=1)
    if not out:
        return doubts
    parsed = extract_json(out[0])
    if not isinstance(parsed, list):
        return doubts  # parse failure must not silently drop doubts
    # verbatim contract: a survivor must match an input exactly (full text, not
    # a 120-char prefix) so a reworded/invented doubt cannot slip through.
    allowed = {_fullkey(d["doubt"]) for d in doubts}
    survivors = [d for d in (_clean_doubt(x) for x in parsed) if d and _fullkey(d["doubt"]) in allowed]
    return survivors  # may be empty -> a legitimate "all trivial" verdict


async def resolve_doubt(reasoner, ground_fn, doubt, context):
    kind = doubt.get("kind", "code")
    if kind == "user":
        return _res("NEEDS_HUMAN", "Only the user can decide this (preference/scope/priority).", "user")
    if kind == "world":
        return await _resolve_world(reasoner, ground_fn, doubt)
    return await _resolve_code(reasoner, doubt, context)


async def _resolve_world(reasoner, ground_fn, doubt):
    facts = await ground_fn(doubt["doubt"])
    conf = float(facts.get("confidence", 0.0) or 0.0)
    if not facts.get("facts") or conf < EXA_CONFIDENCE_FLOOR:
        return _res("UNKNOWN", "Insufficient external evidence to assert.", f"exa:confidence={conf}")
    if reasoner.kind == "template":
        top = facts["facts"][0]
        return _res("CONFIRMED", _clip(top.get("claim"), 200), top.get("url"))
    facts_blob = json.dumps(facts["facts"][:5], indent=2)
    user = f"DOUBT:\n{doubt['doubt']}\n\nCITED FACTS (confidence={conf}):\n{facts_blob}\n\nResolve now."
    outs = await reasoner.complete(RESOLVE_WORLD_SYS, user, n=reasoner.panel_size)
    return _vote(outs, default_source=facts["facts"][0].get("url"))


async def _resolve_code(reasoner, doubt, context):
    if not (context or "").strip():
        return _res("NEEDS_HUMAN", "No codebase evidence was provided to resolve this.", "user")
    if reasoner.kind == "template":
        # No model is available to actually read the evidence, so we must NOT
        # assert. Keyword overlap is only a hint; the honest move is to ask.
        relevant = " The provided context looks relevant." if _keyword_hit(doubt["doubt"], context) else ""
        return _res(
            "NEEDS_HUMAN",
            f"No model available to read the evidence, so I won't guess — please confirm.{relevant}",
            "ask-user",
        )
    user = (
        f"DOUBT:\n{doubt['doubt']}\n\nCODEBASE EVIDENCE:\n{_clip(context, 6000)}\n\n"
        "Resolve using only this evidence."
    )
    outs = await reasoner.complete(RESOLVE_CODE_SYS, user, n=reasoner.panel_size)
    return _vote(outs, default_source="codebase-context")


def _vote(outputs, default_source=None):
    """Panel agreement: unanimous -> settled; split -> escalate to the human."""
    parsed = []
    for o in outputs:
        j = extract_json(o)
        if isinstance(j, dict):
            st = str(j.get("status", "")).upper().strip()
            if st in VALID_STATUSES:
                parsed.append((st, str(j.get("resolution", "")).strip(), j.get("citation") or default_source))
    if not parsed:
        return _res("UNKNOWN", "No resolvable answer was produced.", default_source)
    statuses = [p[0] for p in parsed]
    if len(set(statuses)) == 1:
        st, resolution, src = parsed[0]
        return _res(st, resolution or "Resolved.", src)
    # Distinct models disagree -> genuine uncertainty -> surface it to the human.
    summary = ", ".join(sorted(set(statuses)))
    detail = " | ".join(f"{p[0]}: {p[1]}" for p in parsed[:4] if p[1])
    return _res("NEEDS_HUMAN", f"Panel disagreement ({summary}) — needs human judgment. {detail}".strip(),
                "panel-disagreement")


async def revise_plan(reasoner, plan, resolutions):
    if reasoner.kind == "template":
        return plan  # deterministic & stable so the loop converges cleanly
    settled = [r for r in resolutions if r["status"] in ("CONFIRMED", "REFUTED")]
    if not settled and not any(r["status"] in OPEN_STATUSES for r in resolutions):
        return plan
    blob = json.dumps(
        [{"doubt": r["doubt"], "status": r["status"], "resolution": r["resolution"]} for r in resolutions],
        indent=2,
    )
    user = f"CURRENT PLAN:\n{plan}\n\nRESOLVED DOUBTS:\n{blob}\n\nProduce the revised plan."
    out = await reasoner.complete(REVISE_SYS, user, n=1)
    return out[0].strip() if out and out[0].strip() else plan


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
async def run_doubt_loop(prompt, context, max_passes, reasoner, ground_fn) -> dict:
    context = context or ""
    cap = max(1, min(int(max_passes or HARD_CEILING), HARD_CEILING))  # hard ceiling 20
    plan = await draft_plan(reasoner, prompt, context)

    doubt_log: list[dict] = []
    needs_user: list[str] = []
    seen: set[str] = set()
    converged = False
    passes_used = 0

    for p in range(1, cap + 1):
        passes_used = p
        candidates = await generate_doubts(reasoner, plan, prompt, context, seen)
        survivors = await prune_doubts(reasoner, candidates, plan)
        new = [d for d in survivors if _norm(d["doubt"]) not in seen]

        if not new:
            converged = True  # a full pass produced no new load-bearing doubt
            break

        for d in new:
            seen.add(_norm(d["doubt"]))

        resolutions = []
        for d in new:
            r = await resolve_doubt(reasoner, ground_fn, d, context)
            entry = {
                "pass": p,
                "operator": d.get("operator", "assumption"),
                "doubt": d["doubt"],
                "status": r["status"],
                "resolution": r["resolution"],
                "source": r["source"],
            }
            doubt_log.append(entry)
            resolutions.append(entry)
            if r["status"] == "NEEDS_HUMAN":
                needs_user.append(d["doubt"])

        plan = await revise_plan(reasoner, plan, resolutions)

    # dedup needs_user, preserve order
    seen_nu, nu = set(), []
    for q in needs_user:
        k = _norm(q)
        if k not in seen_nu:
            seen_nu.add(k)
            nu.append(q)

    open_doubts = sum(1 for e in doubt_log if e["status"] in OPEN_STATUSES)
    return {
        "passes_used": passes_used,
        "converged": converged,
        "plan": plan,
        "doubt_log": doubt_log,
        "needs_user": nu,
        # transparency extras (not required by the contract, useful to callers):
        "engine": reasoner.name,
        "note": ENGINE_NOTES.get(reasoner.name, ""),
        "open_doubts": open_doubts,
    }


# --------------------------------------------------------------------------- #
# deterministic template path (no LLM, no network)
# --------------------------------------------------------------------------- #
def _template_plan(prompt, context):
    lines = ["# Plan (template fallback — no LLM configured)", f"- Goal: {(prompt or '').strip()}"]
    for ln in [c.strip() for c in (context or "").splitlines() if c.strip()][:6]:
        lines.append(f"- Consider: {_clip(ln, 140)}")
    lines += ["- Define explicit success criteria",
              "- Identify the smallest safe first step",
              "- Verify the result against the criteria"]
    return "\n".join(lines)


_TEMPLATE_OPS = ["assumption", "falsify", "edge_case", "deletion"]


def _template_doubts(plan, context, seen_texts):
    decisions = [
        ln.strip("-*# ").strip()
        for ln in (plan or "").splitlines()
        if ln.strip().startswith(("-", "*")) and ln.strip("-*# ").strip()
    ]
    out = []
    for i, dec in enumerate(decisions[:6]):
        op = _TEMPLATE_OPS[i % len(_TEMPLATE_OPS)]
        prompt_t = next((o["prompt"] for o in OPERATORS if o["id"] == op), "")
        text = f"[{op}] On '{_clip(dec, 90)}': {prompt_t}"
        if _norm(text) in seen_texts:
            continue
        out.append({"operator": op, "doubt": text, "kind": "code" if (context or "").strip() else "user"})
    return out


def _keyword_hit(doubt, context):
    words = {w for w in re.findall(r"[a-zA-Z0-9_]{4,}", (doubt or "").lower()) if w not in _STOP}
    ctx = (context or "").lower()
    return sum(1 for w in words if w in ctx) >= 2

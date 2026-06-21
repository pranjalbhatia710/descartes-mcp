# Descartes

> **doubt, until what remains is certain.**

[![CI](https://github.com/pranjalbhatia710/descartes-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/pranjalbhatia710/descartes-mcp/actions/workflows/ci.yml)

🪶 **[Landing page →](https://pranjalbhatia710.github.io/descartes-mcp/)** &nbsp;·&nbsp; `descartes --epigraph` for a passing thought from the man himself.

An MCP server that, on a prompt, **doubts every decision in a plan, doubts its own
doubts, and answers them — grounding every answer in real evidence** — iterating
until the plan stops producing new load-bearing doubts. Then it hands back a
refined plan plus the *few* decisions only you can make.

The point: no unexamined assumption reaches the work, so hallucination has no
room to hide.

---

## The loop

On a prompt, Descartes runs recursive Cartesian doubt:

1. **Draft** the plan / decisions.
2. **Doubt** every load-bearing decision (one of 11 doubt operators per doubt).
3. **Doubt the doubts** — prune the trivial and manufactured ones.
4. **Answer** each surviving doubt *only from real evidence*:
   - code doubts → resolved from the `context` you pass in,
   - world doubts → resolved via **Exa** (cited, confidence-scored),
   - ungroundable → marked **NEEDS_HUMAN**.
5. **Doubt the answer** — recurse: each settled answer is itself doubted (the
   question behind the question), spawning the next layer of doubts, bounded by
   depth and a global doubt budget.
6. **Bank it** — every grounded finding accumulates into a **knowledge base**
   that feeds the next round of doubting and the final plan.
7. **Revise** the plan with what survived, and **repeat** from step 2.

The output is therefore a **doubt tree** (each entry carries `depth` + `parent`),
not a flat list, plus a `knowledge_base` of accumulated facts.

**Stopping rule:** the loop stops the moment a full pass produces **no new
load-bearing doubt** — that is convergence. It might be pass 3 or pass 9. **20 is
a hard ceiling, never a target**, and recursion is bounded by `DESCARTES_MAX_DEPTH`
(default 3) and `DESCARTES_DOUBT_BUDGET` (default 64). It never manufactures doubt
to keep going.

**Grounding rule:** every answer resolves against real evidence or becomes a
question for you. Low-confidence Exa is never asserted. 20 rounds of
self-answered, ungrounded doubt would be 20 rounds of confident hallucination —
so that is forbidden by construction.

---

## Two engines (auto-selected)

| If you set…           | Descartes runs…                                                                              |
| --------------------- | -------------------------------------------------------------------------------------------- |
| `FIREWORKS_API_KEY`   | a **panel** of distinct Fireworks model families. Agreement = settled; **disagreement = real uncertainty** → the doubt is escalated. |
| *nothing* (in a client that supports MCP sampling) | **Claude alone** — the client's own model runs the whole loop. |
| `OPENROUTER_API_KEY`  | single-model "Claude alone" via OpenRouter (fallback when sampling is unavailable).          |
| *no keys at all*      | a **deterministic operator-template** fallback, so it never hard-fails.                       |

`EXA_API_KEY` is independent: set it to ground world doubts in any mode.

### No keys? It still works — with just Claude

You do **not** need any API keys. Inside an MCP client that supports sampling
(Claude Code), the whole loop runs on **the client's own Claude model** — code
doubts are resolved by reading the `context` you pass in, and anything that
needs an external fact or a human decision is handed back to you.

And if even sampling isn't available, Descartes **does not guess**. It will not
fake-confirm a doubt from keyword matching; it surfaces every load-bearing doubt
as a question in `needs_user` and tells you so. Every `doubt()` result carries an
`engine` and a plain-English `note` describing exactly how it was powered, so a
degraded run is never silent. The one rule it never breaks: *no answer without
evidence — otherwise, ask you.*

---

## 3-step setup

```bash
# 1. install
git clone https://github.com/pranjalbhatia710/descartes-mcp && cd descartes-mcp
pip install -e .

# 2. (optional) bring your own keys — copy names only, fill in your .env
cp .env.example .env        # .env is gitignored; never commit it

# 3. run it (stdio MCP server)
descartes                   # or: descartes --selftest   to prove the loop converges
```

### Add to Claude Code

Add this to your Claude Code MCP config (e.g. `.mcp.json` in your project, or via
`claude mcp add`). Keys live in your shell / config env — **never in committed files**:

```json
{
  "mcpServers": {
    "descartes": {
      "command": "descartes",
      "args": [],
      "env": {
        "FIREWORKS_API_KEY": "${FIREWORKS_API_KEY}",
        "EXA_API_KEY": "${EXA_API_KEY}",
        "OPENROUTER_API_KEY": "${OPENROUTER_API_KEY}"
      }
    }
  }
}
```

Then ask Claude to *"use descartes to doubt this plan"* and pass the relevant
files as context.

---

## Tools

### `doubt(prompt, context="", max_passes=20)`
Runs the loop above. Returns:

```jsonc
{
  "passes_used": 4,
  "converged": true,
  "plan": "<the refined, doubt-hardened plan>",
  "doubt_log": [
    { "id": 1, "pass": 1, "depth": 0, "parent": null,
      "operator": "assumption", "doubt": "...", "kind": "code",
      "status": "CONFIRMED|REFUTED|UNKNOWN|NEEDS_HUMAN",
      "resolution": "...", "source": "..." }
  ],
  "doubt_tree": [ { "depth": 0, "children": [ { "depth": 1, "children": [] } ] } ],
  "knowledge_base": [
    { "claim": "...", "verdict": "CONFIRMED", "evidence": "...", "source": "...", "depth": 0 }
  ],
  "needs_user": [ "<the few decisions only you can make>" ],
  "engine": "claude-sampling",
  "note": "<plain-English: how this run was powered>",
  "max_depth_reached": 2
}
```

`doubt_log` is the flat record (each entry stamped with `depth` + `parent`);
`doubt_tree` is the same data nested; `knowledge_base` is everything Descartes
grounded along the way, which it builds on instead of re-deriving.

`needs_user` is the product: it separates *what Descartes resolved itself* from
*what it genuinely needs you for*. Kept short — the truly blocking ones only.

### `ground(query)`
Exa deep search → cited, confidence-scored facts. Used inside the loop; callable
directly. Low confidence → `status: "UNKNOWN"` (never asserted).

### `verdict(result)`
Pass a `doubt()` result. Returns `{ proceed, blocking_questions }`. `proceed` is
true only if the plan converged with no open load-bearing doubt; otherwise you
get the blocking questions.

---

## Doubt operators

Every doubt is tagged with one operator (shipped as data in
[`descartes/operators.py`](descartes/operators.py)):

`assumption` · `falsify` · `inversion` · `named_source` · `edge_case` ·
`quantify` · `root_cause` · `reversibility` · `second_order` · `define` ·
`deletion`

---

## Benchmarks — how much better is doubting?

The honest metric isn't accuracy (that tracks whatever model you plug in). It is:
*of a plan's load-bearing assumptions, how many reach the work unexamined?* On a
seeded fixture of 6 real-shaped plans with **19 hand-labeled assumptions**
([`benchmark/coverage.py`](benchmark/coverage.py)):

| approach | examined | grounded from evidence | asked of you | reach work **unexamined** |
|---|---|---|---|---|
| ship the plan as-is | 0 (0%) | 0 | 0 | 19 (**100%**) |
| **Descartes** | 19 (100%) | 12 | 7 | 0 (**0%**) |

Unexamined assumptions reaching the work fall from **100% → 0%**. Of the 19 it
surfaces, Descartes grounds **12 itself** and hands you only the **7** that
genuinely need a human. This is the conservative *floor* — deterministic, in the
spirit of [The Asker](https://github.com/pranjalbhatia710/the-asker)'s synthetic
floor. A real model only widens the gap by catching assumptions the labels missed
and recursing deeper.

**Convergence** ([`benchmark/bench.py`](benchmark/bench.py)) — it stops the moment
a pass adds no new doubt, and a runaway is capped:

| scenario | doubt depth | passes | converged |
| --- | --- | --- | --- |
| trivial | 0 | 1 | ✅ |
| moderate | 3 | 4 | ✅ |
| deep | 11 | 12 | ✅ |
| spiral-guard | 40 | **20 (capped)** | ❌ by design |

Convergence is exactly `depth + 1` passes and never exceeds 20. Both benchmarks
run in CI on every push.

---

## Part of a bigger idea — meet The Asker

Descartes has a sibling: **[The Asker](https://github.com/pranjalbhatia710/the-asker)**
— *"Today's AI answers. This one asks."* An RL environment where an agent learns
to understand any subject by asking the sharpest questions, watching a field of
candidates collapse to the truth in as few questions as possible, and beating
frontier models that just guess.

They are two halves of one thesis: **the right question beats the confident
answer.** Guessing is where hallucination comes from; both tools replace it with
questioning.

- **The Asker** asks, to *understand* — it learns, by reward alone, which
  question removes the most uncertainty.
- **Descartes** doubts, to *be certain* — it questions every decision, then
  recursively questions its own answers, until nothing unexamined remains.

Point The Asker at a subject and watch it learn what to ask. Point Descartes at a
plan and watch it refuse to guess.
→ **[github.com/pranjalbhatia710/the-asker](https://github.com/pranjalbhatia710/the-asker)**

---

## Development & tests

```bash
pip install -e ".[dev]"      # pytest + ruff
ruff check descartes/ tests/ benchmark/
pytest -q                    # tests, no network (all backends are mocked)
python -m benchmark.bench    # convergence benchmark (see Benchmarks above)
python -m benchmark.coverage # coverage benchmark (see Benchmarks above)
```

The suite locks down the load-bearing invariants: the loop always terminates and
never exceeds the hard ceiling of 20, it converges as soon as a pass adds no new
doubt, it recurses (questioning its own answers) bounded by depth and a node
budget, nothing is asserted without evidence (ungroundable → `NEEDS_HUMAN`,
low-confidence Exa → `UNKNOWN`), `needs_user` is exactly the `NEEDS_HUMAN` doubts,
and panel disagreement escalates to the human. `tests/test_regressions.py` carries
one guard per bug found by the adversarial audit. CI (`.github/workflows/ci.yml`)
runs lint + tests + the self-test + both benchmarks on Python 3.10–3.13.

## Safety

- **Bring your own keys.** Read from env only (`os.environ.get`), never stored,
  never logged, never committed. `.env` is gitignored; `.env.example` has names
  only.
- Every external call is wrapped in try/except + timeout + retry — one failure
  never kills a run.
- Exa results are cached and time-boxed so a pass stays fast; panel size is
  capped on the live path (`DESCARTES_PANEL_SIZE`).

## License

MIT — see [LICENSE](LICENSE).

# Descartes

> **doubt, until what remains is certain.**

An MCP server that, on a prompt, **doubts every decision in a plan, doubts its own
doubts, and answers them — grounding every answer in real evidence** — iterating
until the plan stops producing new load-bearing doubts. Then it hands back a
refined plan plus the *few* decisions only you can make.

The point: no unexamined assumption reaches the work, so hallucination has no
room to hide.

---

## The loop

On a prompt, Descartes runs iterative Cartesian doubt:

1. **Draft** the plan / decisions.
2. **Doubt** every load-bearing decision (one of 11 doubt operators per doubt).
3. **Doubt the doubts** — prune the trivial and manufactured ones.
4. **Answer** each surviving doubt *only from real evidence*:
   - code doubts → resolved from the `context` you pass in,
   - world doubts → resolved via **Exa** (cited, confidence-scored),
   - ungroundable → marked **NEEDS_HUMAN**.
5. **Revise** the plan with what survived.
6. **Repeat** from step 2.

**Stopping rule:** the loop stops the moment a full pass produces **no new
load-bearing doubt** — that is convergence. It might be pass 3 or pass 9. **20 is
a hard ceiling, never a target.** It never manufactures doubt to keep going.

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

---

## 3-step setup

```bash
# 1. install
git clone https://github.com/descartes-mcp/descartes && cd descartes
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
    { "pass": 1, "operator": "assumption", "doubt": "...",
      "status": "CONFIRMED|REFUTED|UNKNOWN|NEEDS_HUMAN",
      "resolution": "...", "source": "..." }
  ],
  "needs_user": [ "<the few decisions only you can make>" ]
}
```

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

## Development & tests

```bash
pip install -e ".[dev]"      # pytest + ruff
ruff check descartes/ tests/ benchmark/
pytest -q                    # 69 tests, no network (all backends are mocked)
python -m benchmark.bench    # convergence benchmark (also asserts its invariants)
```

The suite locks down the load-bearing invariants — the loop always terminates and
never exceeds the hard ceiling of 20, it converges as soon as a pass adds no new
doubt, nothing is asserted without evidence (ungroundable → `NEEDS_HUMAN`,
low-confidence Exa → `UNKNOWN`), `needs_user` is exactly the `NEEDS_HUMAN` doubts,
and panel disagreement escalates to the human. `tests/test_regressions.py` carries
one guard per bug found by the adversarial audit. CI (`.github/workflows/ci.yml`)
runs lint + tests + the self-test + the benchmark on Python 3.10–3.13.

Benchmark (instant stub reasoner — measures loop machinery, not network):

| scenario | doubt depth | passes | converged |
| --- | --- | --- | --- |
| trivial | 0 | 1 | ✅ |
| moderate | 3 | 4 | ✅ |
| deep | 11 | 12 | ✅ |
| spiral-guard | 40 | **20 (capped)** | ❌ by design |

Convergence is exactly `depth + 1` passes and never exceeds 20 — a 40-doubt
"spiral" is capped at 20 and reported as not converged.

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

"""Reasoning backends — the fork between a Fireworks panel and Claude alone.

Every backend exposes the same primitive:

    async def complete(system, user, n=1) -> list[str]

Single-perspective backends return a 1-element list (ignoring n). The Fireworks
panel returns up to n completions from *distinct model families* so the loop can
read agreement as "settled" and disagreement as "real uncertainty".

Selection (degrade, never hard-fail):
  FIREWORKS_API_KEY        -> FireworksPanel        (disagreement = the detector)
  else, MCP client present -> ClaudeSamplingReasoner (the client's own model)
  else OPENROUTER_API_KEY  -> OpenRouterReasoner     (single-model Claude alone)
  else                     -> TemplateReasoner       (deterministic, no LLM)
"""
import asyncio
import os

FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Distinct families on Fireworks — disagreement *across families* is the
# uncertainty detector, so the default panel mixes Llama / Qwen / DeepSeek /
# Mixtral. Swappable constant: verify / update ids at https://fireworks.ai/models
FIREWORKS_MODELS = [
    "accounts/fireworks/models/llama-v3p3-70b-instruct",
    "accounts/fireworks/models/qwen2p5-72b-instruct",
    "accounts/fireworks/models/deepseek-v3",
    "accounts/fireworks/models/mixtral-8x22b-instruct",
    "accounts/fireworks/models/llama-v3p1-405b-instruct",
]

def _env_float(name, default, minimum=None):
    """Parse a float env var; a bad value degrades to the default (never crashes)."""
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = float(default)
    return max(minimum, value) if minimum is not None else value


def _env_int(name, default, minimum=None):
    """Parse an int env var; a bad value degrades to the default (never crashes)."""
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, value) if minimum is not None else value


# A non-positive timeout would make every request time out instantly; floor it.
DEFAULT_TIMEOUT = _env_float("DESCARTES_TIMEOUT", 45, minimum=1.0)
MAX_TOKENS = _env_int("DESCARTES_MAX_TOKENS", 1200, minimum=1)


class Reasoner:
    kind = "llm"
    name = "base"
    panel_size = 1

    async def complete(self, system: str, user: str, n: int = 1) -> list[str]:
        raise NotImplementedError


async def _chat(url, api_key, model, system, user, timeout, extra_headers=None):
    """One OpenAI-compatible chat completion with timeout + one retry."""
    import httpx  # lazy: keeps the no-key template path dependency-light

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "max_tokens": MAX_TOKENS,
    }
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001 — one model failing must not kill the pass
            await asyncio.sleep(0.6 * (attempt + 1))
    return None


class FireworksPanel(Reasoner):
    name = "fireworks-panel"

    def __init__(self, api_key, models=None, panel_size=None, concurrency=4, timeout=DEFAULT_TIMEOUT):
        self.api_key = api_key
        self.models = models or FIREWORKS_MODELS
        size = panel_size or _env_int("DESCARTES_PANEL_SIZE", 3, minimum=1)
        self.panel_size = max(1, min(size, len(self.models)))
        self._sem = asyncio.Semaphore(concurrency)
        self.timeout = timeout

    async def _one(self, model, system, user):
        async with self._sem:
            return await _chat(FIREWORKS_URL, self.api_key, model, system, user, self.timeout)

    async def complete(self, system, user, n=1):
        count = max(1, min(n, len(self.models)))
        models = self.models[:count]
        results = await asyncio.gather(*[self._one(m, system, user) for m in models])
        return [r for r in results if r]


class OpenRouterReasoner(Reasoner):
    name = "openrouter"

    def __init__(self, api_key, model=None, timeout=DEFAULT_TIMEOUT):
        self.api_key = api_key
        self.model = model or os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
        self.timeout = timeout

    async def complete(self, system, user, n=1):
        text = await _chat(
            OPENROUTER_URL, self.api_key, self.model, system, user, self.timeout,
            extra_headers={"HTTP-Referer": "https://github.com/pranjalbhatia710/descartes-mcp",
                           "X-Title": "descartes-mcp"},
        )
        return [text] if text else []


class ClaudeSamplingReasoner(Reasoner):
    """The MCP client's own model, via MCP sampling — 'Claude alone'.

    Availability depends on the client supporting sampling; if it does not,
    complete() returns [] and the caller degrades to OpenRouter / template.
    """
    name = "claude-sampling"

    def __init__(self, ctx):
        self.ctx = ctx

    async def complete(self, system, user, n=1):
        try:
            from mcp.types import SamplingMessage, TextContent
            res = await self.ctx.session.create_message(
                messages=[SamplingMessage(role="user", content=TextContent(type="text", text=user))],
                system_prompt=system,
                max_tokens=MAX_TOKENS,
                temperature=0.4,
            )
            content = getattr(res, "content", None)
            text = getattr(content, "text", None)
            return [text] if text else []
        except Exception:  # noqa: BLE001 — unsupported / transient: let the caller degrade
            return []


class TemplateReasoner(Reasoner):
    """No-key deterministic fallback. Never calls the network; never hard-fails."""
    kind = "template"
    name = "template-fallback"

    async def complete(self, system, user, n=1):
        return []


def select_reasoner(ctx=None) -> Reasoner:
    if os.environ.get("FIREWORKS_API_KEY"):
        return FireworksPanel(os.environ["FIREWORKS_API_KEY"])
    if ctx is not None:
        return ClaudeSamplingReasoner(ctx)
    if os.environ.get("OPENROUTER_API_KEY"):
        return OpenRouterReasoner(os.environ["OPENROUTER_API_KEY"])
    return TemplateReasoner()

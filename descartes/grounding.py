"""Exa-grounded fact retrieval for the doubt loop.

A `world` doubt is never resolved by guessing. It is resolved against cited
Exa results with a confidence score. Low confidence is reported as UNKNOWN and
never asserted. Results are cached (Exa is the expensive call) and every call
has a tight timeout + one retry so a single failure never kills a pass.
"""
import asyncio
import os
import time

EXA_URL = "https://api.exa.ai/search"


def _env_float(name, default, minimum=None):
    """Parse a float env var; a bad value degrades to the default (never crashes)."""
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = float(default)
    return max(minimum, value) if minimum is not None else value


def _coerce_score(score):
    """Exa relevance score -> float in a way that never loses a valid number.

    bool is excluded (True would otherwise read as 1.0 confidence) and numeric
    strings like "0.92" are parsed rather than silently dropped to 0.0.
    """
    if isinstance(score, bool):
        return 0.0
    if isinstance(score, (int, float)):
        return float(score)
    if isinstance(score, str):
        try:
            return float(score.strip())
        except ValueError:
            return 0.0
    return 0.0


CACHE_TTL = _env_float("DESCARTES_EXA_CACHE_TTL", 3600, minimum=0.0)
EXA_TIMEOUT = _env_float("DESCARTES_EXA_TIMEOUT", 15, minimum=1.0)

# query -> (monotonic_ts, result)
_CACHE: dict[str, tuple[float, dict]] = {}


def _cached(query: str):
    hit = _CACHE.get(query)
    if hit and (time.monotonic() - hit[0]) < CACHE_TTL:
        return hit[1]
    return None


async def ground(query: str, num_results: int = 5) -> dict:
    """Exa deep search -> cited, confidence-scored facts.

    Returns: {query, confidence (0..1), facts: [{claim, url, title, score}],
              status: CONFIRMED|UNKNOWN, note?/error?}
    """
    query = (query or "").strip()
    if not query:
        return {"query": query, "confidence": 0.0, "facts": [], "status": "UNKNOWN", "note": "empty query"}

    cached = _cached(query)
    if cached is not None:
        return cached

    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        # Degrade, never hard-fail: no key means no external grounding.
        return {"query": query, "confidence": 0.0, "facts": [], "status": "UNKNOWN",
                "note": "EXA_API_KEY not set — external doubts cannot be grounded"}

    import httpx  # lazy: the no-key/template path needs no network deps

    payload = {
        "query": query,
        "type": "auto",
        "numResults": max(1, min(num_results, 10)),
        "contents": {"text": {"maxCharacters": 800}, "highlights": True},
    }
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    last_err = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=EXA_TIMEOUT) as client:
                resp = await client.post(EXA_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            facts, scores = [], []
            for item in data.get("results", []) or []:
                score = _coerce_score(item.get("score"))
                scores.append(score)
                highlights = item.get("highlights") or []
                claim = highlights[0] if highlights else (item.get("text") or "")[:300]
                facts.append({
                    "claim": (claim or "").strip(),
                    "url": item.get("url"),
                    "title": item.get("title"),
                    "score": round(score, 3),
                })

            confidence = round(max(scores), 3) if scores else 0.0
            status = "CONFIRMED" if (facts and confidence > 0) else "UNKNOWN"
            result = {"query": query, "confidence": confidence, "facts": facts, "status": status}
            _CACHE[query] = (time.monotonic(), result)
            return result
        except Exception as exc:  # noqa: BLE001 — one failure must not kill the pass
            last_err = str(exc)
            await asyncio.sleep(0.5 * (attempt + 1))

    return {"query": query, "confidence": 0.0, "facts": [], "status": "UNKNOWN", "error": last_err}

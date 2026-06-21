"""Shared test doubles for the Descartes suite — no network, fully deterministic."""
import json

from descartes.loop import (
    DRAFT_SYS,
    GEN_SYS,
    PRUNE_SYS,
    RESOLVE_CODE_SYS,
    RESOLVE_WORLD_SYS,
    REVISE_SYS,
)
from descartes.panel import Reasoner


async def stub_ground(query):
    """A grounder that always returns low confidence (nothing to assert)."""
    return {"query": query, "confidence": 0.0, "facts": [], "status": "UNKNOWN"}


def strong_ground(confidence=0.9, url="https://example.com/x", claim="a cited fact"):
    async def _g(query):
        return {
            "query": query,
            "confidence": confidence,
            "facts": [{"claim": claim, "url": url, "title": "T", "score": confidence}],
            "status": "CONFIRMED" if confidence > 0 else "UNKNOWN",
        }
    return _g


def _candidates_from_prune_user(user):
    """Echo back the candidate doubts a prune prompt was given (verbatim path)."""
    after = user.split("CANDIDATE DOUBTS:\n", 1)
    if len(after) < 2:
        return "[]"
    return after[1].split("\n\nReturn", 1)[0].strip()


class DepthReasoner(Reasoner):
    """Single-perspective LLM stub that emits exactly `depth` distinct, *new*
    doubts (one per pass), each resolvable to CONFIRMED from code. With the
    `seen` dedup + convergence rule this makes the loop run exactly depth+1
    passes and then converge. Used for termination + benchmark tests.
    """
    kind = "llm"
    name = "depth-stub"

    def __init__(self, depth=3, panel_size=1, doubt_kind="code"):
        self.depth = depth
        self.panel_size = panel_size
        self.doubt_kind = doubt_kind
        self.gen_calls = 0

    async def complete(self, system, user, n=1):
        if system == DRAFT_SYS:
            return ["# Plan\n- decision alpha\n- decision beta"]
        if system == GEN_SYS:
            self.gen_calls += 1
            if self.gen_calls <= self.depth:
                doubt = {
                    "operator": "assumption",
                    "doubt": f"is assumption number {self.gen_calls} verified?",
                    "kind": self.doubt_kind,
                }
                return [json.dumps([doubt])]
            return ["[]"]  # nothing new -> convergence
        if system == PRUNE_SYS:
            return [_candidates_from_prune_user(user)]
        if system in (RESOLVE_CODE_SYS, RESOLVE_WORLD_SYS):
            # citation=null -> the loop falls back to its own default_source
            # (Exa url for world doubts, "codebase-context" for code doubts).
            return ['{"status": "CONFIRMED", "resolution": "found in evidence", "citation": null}']
        # revise
        return ["# Plan (revised)\n- decision alpha\n- decision beta"]


class PanelStub(Reasoner):
    """Multi-perspective stub: `complete` returns one canned string per panel
    member, so loop-level agreement/disagreement logic can be exercised.
    `resolve_outputs` is the list of JSON strings each member returns when
    resolving a doubt.
    """
    kind = "llm"
    name = "panel-stub"

    def __init__(self, doubts, resolve_outputs, panel_size=3):
        self.panel_size = panel_size
        self._doubts_json = json.dumps(doubts)
        self._resolve_outputs = resolve_outputs
        self.gen_calls = 0

    async def complete(self, system, user, n=1):
        if system == DRAFT_SYS:
            return ["# Plan\n- only decision"]
        if system == GEN_SYS:
            self.gen_calls += 1
            return [self._doubts_json] if self.gen_calls == 1 else ["[]"]
        if system == PRUNE_SYS:
            return [_candidates_from_prune_user(user)]
        if system in (RESOLVE_CODE_SYS, RESOLVE_WORLD_SYS):
            return list(self._resolve_outputs)  # one per panel member
        return ["# Plan (revised)\n- only decision"]


class ScriptedReasoner(Reasoner):
    """Maps a system-prompt substring -> outputs (list[str] or callable).

    Lets a test drive one step in isolation, e.g. {PRUNE_SYS: [...]}.
    """
    kind = "llm"
    name = "scripted"

    def __init__(self, mapping, panel_size=1):
        self.mapping = mapping
        self.panel_size = panel_size

    async def complete(self, system, user, n=1):
        for marker, val in self.mapping.items():
            if marker in system:
                return val(system, user) if callable(val) else list(val)
        return []


# ---- fake httpx (for panel._chat and grounding.ground network tests) ---------
class FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeAsyncClient:
    """Drop-in for httpx.AsyncClient. Set `FakeAsyncClient.handler` to a callable
    (url, json, headers) -> FakeResp (or raising) before the test runs."""
    handler = None
    calls = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        FakeAsyncClient.calls.append({"url": url, "json": json, "headers": headers})
        return FakeAsyncClient.handler(url, json, headers)


def install_fake_httpx(monkeypatch, handler):
    """Patch httpx.AsyncClient with the fake and reset call log."""
    import httpx
    FakeAsyncClient.handler = handler
    FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    return FakeAsyncClient


# ---- fake MCP-sampling client: "just Claude", no API keys --------------------
def claude_sampling_ctx():
    """A fake MCP client whose sampling behaves like a competent Claude: it
    drafts, raises one code doubt, prunes verbatim, and resolves from the
    provided context. Lets tests exercise the zero-key 'Claude alone' path."""
    state = {"gen": 0}

    def respond(system, messages):
        if system == DRAFT_SYS:
            return "# Plan\n- decision alpha\n- decision beta"
        if system == GEN_SYS:
            state["gen"] += 1
            if state["gen"] == 1:
                return json.dumps([{"operator": "assumption",
                                    "doubt": "is the cache key stable?", "kind": "code"}])
            return "[]"
        if system == PRUNE_SYS:
            user = messages[0].content.text if messages else ""
            after = user.split("CANDIDATE DOUBTS:\n", 1)
            return after[1].split("\n\nReturn", 1)[0] if len(after) > 1 else "[]"
        if system in (RESOLVE_CODE_SYS, RESOLVE_WORLD_SYS):
            return '{"status": "CONFIRMED", "resolution": "the evidence shows it", "citation": null}'
        if system == REVISE_SYS:
            return "# Plan (revised)\n- decision alpha\n- decision beta"
        return "ok"  # probe + anything else

    class _Content:
        def __init__(self, text):
            self.text = text

    class _Result:
        def __init__(self, text):
            self.content = _Content(text)

    class _Session:
        async def create_message(self, **kw):
            return _Result(respond(kw.get("system_prompt", ""), kw.get("messages") or []))

    class _Ctx:
        def __init__(self):
            self.session = _Session()

    return _Ctx()

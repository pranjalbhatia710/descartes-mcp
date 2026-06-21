"""Backend selection + the HTTP/sampling primitives, fully mocked (no network)."""
import asyncio

from helpers import install_fake_httpx

from descartes import panel
from descartes.panel import (
    FIREWORKS_MODELS,
    ClaudeSamplingReasoner,
    FireworksPanel,
    OpenRouterReasoner,
    TemplateReasoner,
    _chat,
    select_reasoner,
)

run = asyncio.run


# --------------------------------------------------------------------------- #
# selection precedence
# --------------------------------------------------------------------------- #
def test_select_no_keys_no_ctx_is_template():
    assert isinstance(select_reasoner(None), TemplateReasoner)


def test_select_fireworks_wins(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    assert isinstance(select_reasoner(object()), FireworksPanel)


def test_select_sampling_before_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    # ctx present + no fireworks -> sampling takes precedence over openrouter
    assert isinstance(select_reasoner(object()), ClaudeSamplingReasoner)


def test_select_openrouter_when_no_ctx(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    assert isinstance(select_reasoner(None), OpenRouterReasoner)


def test_panel_size_clamped(monkeypatch):
    monkeypatch.setenv("DESCARTES_PANEL_SIZE", "99")
    p = FireworksPanel("fw")
    assert p.panel_size == len(FIREWORKS_MODELS)
    monkeypatch.setenv("DESCARTES_PANEL_SIZE", "0")
    assert FireworksPanel("fw").panel_size == 1


# --------------------------------------------------------------------------- #
# FireworksPanel.complete over (fake) HTTP
# --------------------------------------------------------------------------- #
def test_panel_returns_one_completion_per_model(monkeypatch):
    def handler(url, json, headers):
        from helpers import FakeResp
        model = json["model"]
        return FakeResp({"choices": [{"message": {"content": f"hi:{model[-12:]}"}}]})
    install_fake_httpx(monkeypatch, handler)
    p = FireworksPanel("fw")
    out = run(p.complete("sys", "user", n=3))
    assert len(out) == 3
    assert all(o.startswith("hi:") for o in out)


def test_panel_filters_failed_models(monkeypatch):
    def handler(url, json, headers):
        from helpers import FakeResp
        if "qwen" in json["model"]:
            raise RuntimeError("model down")
        return FakeResp({"choices": [{"message": {"content": "ok"}}]})
    install_fake_httpx(monkeypatch, handler)
    # patch sleep so the failing model's retry doesn't add real delay
    monkeypatch.setattr(panel.asyncio, "sleep", _noop)
    p = FireworksPanel("fw")
    out = run(p.complete("sys", "user", n=2))  # llama + qwen; qwen fails
    assert out == ["ok"]


# --------------------------------------------------------------------------- #
# _chat retry semantics
# --------------------------------------------------------------------------- #
async def _noop(*a, **k):
    return None


def test_chat_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(url, json, headers):
        from helpers import FakeResp
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return FakeResp({"choices": [{"message": {"content": "recovered"}}]})
    install_fake_httpx(monkeypatch, handler)
    monkeypatch.setattr(panel.asyncio, "sleep", _noop)
    out = run(_chat("u", "k", "m", "s", "u", 5))
    assert out == "recovered" and calls["n"] == 2


def test_chat_returns_none_after_exhausting_retries(monkeypatch):
    def handler(url, json, headers):
        raise RuntimeError("always down")
    install_fake_httpx(monkeypatch, handler)
    monkeypatch.setattr(panel.asyncio, "sleep", _noop)
    assert run(_chat("u", "k", "m", "s", "u", 5)) is None


# --------------------------------------------------------------------------- #
# OpenRouter + sampling + template complete() contracts
# --------------------------------------------------------------------------- #
def test_openrouter_complete(monkeypatch):
    def handler(url, json, headers):
        from helpers import FakeResp
        assert "openrouter" in url
        return FakeResp({"choices": [{"message": {"content": "claude says hi"}}]})
    install_fake_httpx(monkeypatch, handler)
    assert run(OpenRouterReasoner("or").complete("s", "u")) == ["claude says hi"]


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResult:
    def __init__(self, text):
        self.content = _FakeContent(text)


class _FakeSession:
    def __init__(self, text=None, fail=False):
        self._text = text
        self._fail = fail

    async def create_message(self, **kw):
        if self._fail:
            raise RuntimeError("sampling unsupported")
        return _FakeResult(self._text)


class FakeCtx:
    def __init__(self, text=None, fail=False):
        self.session = _FakeSession(text, fail)


def test_sampling_success_returns_text():
    r = ClaudeSamplingReasoner(FakeCtx(text="sampled answer"))
    assert run(r.complete("s", "u")) == ["sampled answer"]


def test_sampling_failure_returns_empty():
    r = ClaudeSamplingReasoner(FakeCtx(fail=True))
    assert run(r.complete("s", "u")) == []


def test_template_complete_is_empty():
    assert run(TemplateReasoner().complete("s", "u")) == []


def test_models_list_is_distinct_families():
    # the panel's whole point is family diversity; guard against accidental dupes
    assert len(FIREWORKS_MODELS) == len(set(FIREWORKS_MODELS))
    assert len(FIREWORKS_MODELS) >= 3

"""Shared fixtures: a clean environment and clean Exa cache for every test."""
import os
import sys

import pytest

# Make tests/helpers and the repo-root `benchmark` package importable regardless of cwd.
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from descartes import grounding  # noqa: E402

_KEYS = (
    "FIREWORKS_API_KEY", "EXA_API_KEY", "OPENROUTER_API_KEY",
    "DESCARTES_PANEL_SIZE", "OPENROUTER_MODEL", "DESCARTES_EXA_FLOOR",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No ambient keys leak into a test; Exa cache starts empty."""
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)
    grounding._CACHE.clear()
    yield
    grounding._CACHE.clear()

"""Every test runs deterministic: offline LLM, no web, fixed 'today' (NFR-01)."""
import pytest


@pytest.fixture(autouse=True)
def offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.setenv("ENABLE_WEB_TOOLS", "false")
    monkeypatch.setenv("GETNET_TODAY", "2026-10-30")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

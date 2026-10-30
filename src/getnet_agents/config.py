"""Runtime settings read from the environment (12-factor). Read on every call so tests can monkeypatch env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE = ROOT / "data" / "knowledge" / "corpus.jsonl"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    anthropic_api_key: str
    router_model: str
    agent_model: str
    llm_timeout_s: float
    llm_max_retries: int
    enable_web_tools: bool
    knowledge_path: Path
    retrieval_min_score: float
    router_min_confidence: float
    grounding_threshold: float
    log_level: str


def get_settings() -> Settings:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    provider = os.getenv("LLM_PROVIDER", "offline").strip().lower() or "offline"
    if provider not in {"offline", "anthropic"}:
        provider = "offline"
    return Settings(
        llm_provider=provider,
        anthropic_api_key=key,
        router_model=os.getenv("ROUTER_MODEL", "claude-haiku-5-5"),
        agent_model=os.getenv("AGENT_MODEL", "claude-sonnet-5-5"),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "20")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        enable_web_tools=_bool("ENABLE_WEB_TOOLS", True),
        knowledge_path=Path(os.getenv("KNOWLEDGE_PATH", str(DEFAULT_KNOWLEDGE))),
        retrieval_min_score=float(os.getenv("RETRIEVAL_MIN_SCORE", "0.35")),
        router_min_confidence=float(os.getenv("ROUTER_MIN_CONFIDENCE", "0.45")),
        grounding_threshold=float(os.getenv("GROUNDING_THRESHOLD", "0.6")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )

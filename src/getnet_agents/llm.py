"""LLM layer (FR-11, NFR-02, ADR-02).

- `AnthropicLLM`: Claude via the official SDK, with client timeout, SDK retries plus one extra backoff
  round, and typed error handling. Raises `LLMError` when it gives up, so agents fall back to their
  deterministic path.
- `OfflineLLM`: `enabled = False`. Agents see it and use their deterministic implementation
  (rules + extractive generation). Default provider and automatic fallback.
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .config import PROMPTS_DIR, Settings, get_settings
from .observability import inc, mark_llm_used, span


class LLMError(RuntimeError):
    """The LLM could not produce a usable answer (after retries)."""


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


@dataclass
class ToolLoopResult:
    text: str
    calls: list[dict[str, Any]] = field(default_factory=list)  # {name, args, result, latency_ms}


class OfflineLLM:
    mode = "offline"
    enabled = False


class AnthropicLLM:
    mode = "anthropic"
    enabled = True

    def __init__(self, settings: Settings) -> None:
        import anthropic  # imported lazily: offline mode never needs the SDK

        self._anthropic = anthropic
        self.settings = settings
        self.client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,  # SDK retries 408/409/429/5xx + connection errors
        )

    # ------------------------------------------------------------------ low level
    def _create(self, *, role: str, **kwargs: Any) -> Any:
        model = self.settings.router_model if role == "router" else self.settings.agent_model
        a = self._anthropic
        last: Exception | None = None
        for attempt in range(2):  # one extra round on top of SDK retries, with backoff
            with span("llm", model=model, role=role, attempt=attempt) as info:
                try:
                    resp = self.client.messages.create(model=model, **kwargs)
                    info["stop_reason"] = resp.stop_reason
                    info["input_tokens"] = resp.usage.input_tokens
                    info["output_tokens"] = resp.usage.output_tokens
                    inc("getnet_llm_calls_total", "LLM calls by model and status", model=model, status="ok")
                    if resp.stop_reason == "refusal":
                        raise LLMError("model refused")
                    mark_llm_used()
                    return resp
                except (a.AuthenticationError, a.PermissionDeniedError, a.BadRequestError, a.NotFoundError) as exc:
                    inc("getnet_llm_calls_total", "LLM calls by model and status", model=model, status="fatal")
                    raise LLMError(f"non-retryable: {type(exc).__name__}") from exc
                except (a.RateLimitError, a.APITimeoutError, a.APIConnectionError, a.InternalServerError) as exc:
                    inc("getnet_llm_calls_total", "LLM calls by model and status", model=model, status="retryable")
                    info["error"] = type(exc).__name__
                    last = exc
                except a.APIStatusError as exc:
                    inc("getnet_llm_calls_total", "LLM calls by model and status", model=model, status="error")
                    raise LLMError(f"api error {exc.status_code}") from exc
            time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"gave up after retries: {type(last).__name__}")

    @staticmethod
    def _text(resp: Any) -> str:
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    # ------------------------------------------------------------------ high level
    def complete(self, system: str, user: str, *, role: str = "agent", max_tokens: int = 1024) -> str:
        resp = self._create(
            role=role, system=system, max_tokens=max_tokens, messages=[{"role": "user", "content": user}]
        )
        text = self._text(resp)
        if not text:
            raise LLMError("empty completion")
        return text

    def complete_json(self, system: str, user: str, *, role: str = "router") -> dict[str, Any]:
        text = self.complete(system, user, role=role, max_tokens=512)
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise LLMError("no JSON object in completion")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("invalid JSON from model") from exc

    def tool_loop(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        execute: Callable[[str, dict[str, Any]], tuple[dict[str, Any], float]],
        *,
        max_steps: int = 4,
    ) -> ToolLoopResult:
        """Manual agentic loop: call tools until the model ends its turn or `max_steps` is reached."""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        calls: list[dict[str, Any]] = []
        for _ in range(max_steps):
            resp = self._create(role="agent", system=system, tools=tools, max_tokens=2048, messages=messages)
            if resp.stop_reason != "tool_use":
                return ToolLoopResult(self._text(resp), calls)
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                args = dict(block.input) if isinstance(block.input, dict) else {}
                result, latency_ms = execute(block.name, args)
                calls.append({"name": block.name, "args": args, "result": result, "latency_ms": latency_ms})
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str)[:6000],
                        "is_error": not result.get("ok", False),
                    }
                )
            messages.append({"role": "user", "content": results})  # all results in one message
        raise LLMError("tool loop exceeded max_steps")


def get_llm(settings: Settings | None = None) -> OfflineLLM | AnthropicLLM:
    s = settings or get_settings()
    if s.llm_provider == "anthropic" and s.anthropic_api_key:
        try:
            return AnthropicLLM(s)
        except Exception:  # SDK missing or misconfigured → deterministic path (Article II)
            return OfflineLLM()
    return OfflineLLM()

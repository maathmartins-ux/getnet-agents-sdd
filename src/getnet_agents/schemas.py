"""Public contracts of the system (spec.md §5). Every module and fork depends on these.

Changing a field here is a spec change: update specs/001-multi-agent-support/spec.md first.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

AgentName = Literal["router", "knowledge", "support", "escalation"]
Intent = Literal[
    "product_info",
    "general_web",
    "account_support",
    "technical_support",
    "human_request",
    "out_of_scope",
    "unsafe",
]
Language = Literal["pt", "en"]
Mode = Literal["offline", "anthropic"]

AGENTS: tuple[str, ...] = ("router", "knowledge", "support", "escalation")
INTENTS: tuple[str, ...] = (
    "product_info",
    "general_web",
    "account_support",
    "technical_support",
    "human_request",
    "out_of_scope",
    "unsafe",
)


class ToolResult(TypedDict):
    """Uniform return of every tool. Tools never raise; failures come back as ok=False."""

    ok: bool
    data: dict[str, Any] | list[Any]
    source: str
    error: str | None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(..., min_length=1, max_length=128)


class Source(BaseModel):
    title: str
    url: str
    score: float


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any]
    ok: bool
    latency_ms: float


class Handoff(BaseModel):
    required: bool
    reason: str
    ticket_id: str | None = None


class Guardrail(BaseModel):
    blocked: bool
    category: str
    reason: str


class ChatResponse(BaseModel):
    trace_id: str
    answer: str
    language: Language
    route: list[str]
    intent: Intent
    confidence: float = Field(..., ge=0.0, le=1.0)
    sources: list[Source] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    handoff: Handoff | None = None
    guardrail: Guardrail | None = None
    mode: Mode
    latency_ms: float


class RoutingDecision(BaseModel):
    """Output of the Router agent: what the user wants and which agents run, in order."""

    intent: Intent
    language: Language
    plan: list[AgentName]
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = ""


class AgentResult(BaseModel):
    """What each specialist agent hands back to the orchestrator (internal message)."""

    agent: AgentName
    answer: str = ""
    sources: list[Source] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    grounded: bool = False
    needs_handoff: bool = False
    handoff_reason: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)

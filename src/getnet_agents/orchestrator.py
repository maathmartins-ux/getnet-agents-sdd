"""Orchestrator (FR-07, FR-10): guardrails → Router → specialist agents → escalation → response.

Agents communicate through typed in-process messages (ADR-01): the Router returns a `RoutingDecision`,
each specialist returns an `AgentResult`, and `facts` from one agent feed the next (Support → Knowledge).
"""
from __future__ import annotations

import time

from .agents import escalation, knowledge, support
from .agents.router import detect_language, needs_escalation, route
from .config import get_settings
from .guardrails import check_input, mask_pii
from .llm import AnthropicLLM, OfflineLLM, get_llm
from .observability import configure_logging, inc, llm_was_used, log_event, new_trace, observe, span
from .schemas import AgentResult, ChatRequest, ChatResponse, Guardrail, Handoff, RoutingDecision, Source, ToolCall

REFUSALS = {
    "unsafe": (
        "Não posso ajudar com esse pedido. Posso responder dúvidas sobre produtos Getnet, sua conta e suas vendas.",
        "I can't help with that request. I can answer questions about Getnet products, your account and your sales.",
    ),
    "out_of_scope": (
        "Esse assunto está fora do que posso atender. Sou o assistente da Getnet: posso ajudar com maquininhas, "
        "recebimentos, Pix, antecipação, link de pagamento e dados da sua conta.",
        "That's outside what I can help with. I'm Getnet's assistant: I can help with card machines, settlements, "
        "Pix, receivables advance, Payment Link and your account data.",
    ),
}


class Orchestrator:
    def __init__(self, llm: OfflineLLM | AnthropicLLM | None = None) -> None:
        configure_logging(get_settings().log_level)
        self.llm = llm or get_llm()

    def handle(self, req: ChatRequest) -> ChatResponse:
        start = time.perf_counter()
        trace_id = new_trace()
        with span("request", user_id=req.user_id) as info:
            response = self._handle(req, trace_id, start)
            info.update(intent=response.intent, route=response.route, handoff=bool(response.handoff))
        outcome = "blocked" if response.guardrail and response.guardrail.blocked else (
            "handoff" if response.handoff else "answered")
        inc("getnet_requests_total", "Chat requests by intent and outcome", intent=response.intent, outcome=outcome)
        observe("getnet_request_duration_seconds", "End-to-end /chat latency", response.latency_ms / 1000, intent=response.intent)
        log_event("request_completed", intent=response.intent, route=response.route, confidence=response.confidence,
                  outcome=outcome, sources=len(response.sources), tools=[t.name for t in response.tool_calls],
                  mode=response.mode, latency_ms=response.latency_ms)
        return response

    # ------------------------------------------------------------------
    def _blocked(self, trace_id: str, start: float, lang: str, intent: str, category: str, reason: str) -> ChatResponse:
        inc("getnet_guardrail_blocks_total", "Requests blocked by guardrails", category=category)
        msg = REFUSALS["unsafe" if intent == "unsafe" else "out_of_scope"][0 if lang == "pt" else 1]
        return ChatResponse(
            trace_id=trace_id, answer=msg, language=lang, route=["router"], intent=intent, confidence=1.0,
            guardrail=Guardrail(blocked=True, category=category, reason=reason),
            mode="anthropic" if llm_was_used() else "offline", latency_ms=_ms(start),
        )

    def _handle(self, req: ChatRequest, trace_id: str, start: float) -> ChatResponse:
        check = check_input(req.message, req.user_id)  # PII masked from here on (BR-07)
        if check.blocked:
            lang = detect_language(check.text)
            intent = "out_of_scope" if check.category == "out_of_scope" else "unsafe"
            return self._blocked(trace_id, start, lang, intent, check.category or intent, check.reason)

        with span("agent", agent="router") as info:
            decision: RoutingDecision = route(check.text, self.llm)
            info.update(intent=decision.intent, language=decision.language, confidence=decision.confidence)
        if decision.intent in {"out_of_scope", "unsafe"}:  # flagged by the LLM router
            return self._blocked(trace_id, start, decision.language, decision.intent, decision.intent, decision.rationale)

        escalate_reason = "low_confidence" if needs_escalation(decision) else None
        results: list[AgentResult] = []
        facts: dict = {}
        for agent in decision.plan[1:]:
            with span("agent", agent=agent) as info:
                if agent == "support":
                    result = support.run(check.text, req.user_id, decision, self.llm)
                elif agent == "knowledge":
                    result = knowledge.run(check.text, decision, self.llm, facts)
                else:
                    result = escalation.run(check.text, req.user_id, decision, "human_request")
                info.update(grounded=result.grounded, needs_handoff=result.needs_handoff, tools=len(result.tool_calls))
            results.append(result)
            facts.update(result.facts)
            if result.needs_handoff and agent != "escalation":
                escalate_reason = escalate_reason or result.handoff_reason

        if escalate_reason and not any(r.agent == "escalation" for r in results):
            with span("agent", agent="escalation", reason=escalate_reason):
                results.append(escalation.run(check.text, req.user_id, decision, escalate_reason))

        esc = next((r for r in results if r.agent == "escalation"), None)
        handoff = None
        if esc:
            handoff = Handoff(required=True, reason=esc.handoff_reason, ticket_id=esc.facts.get("ticket_id"))
            inc("getnet_handoffs_total", "Human handoffs by reason", reason=esc.handoff_reason)

        answer = mask_pii("\n\n".join(r.answer for r in results if r.answer))[0]
        sources: dict[str, Source] = {}
        for r in results:
            for s in r.sources:
                sources.setdefault(s.url, s)
        tool_calls: list[ToolCall] = [c for r in results for c in r.tool_calls]
        guardrail = (Guardrail(blocked=False, category="pii_redacted", reason=check.reason)
                     if check.category == "pii_redacted" else None)
        return ChatResponse(
            trace_id=trace_id, answer=answer, language=decision.language,
            route=["router"] + list(dict.fromkeys(r.agent for r in results)),
            intent=decision.intent, confidence=decision.confidence, sources=list(sources.values()),
            tool_calls=tool_calls, handoff=handoff, guardrail=guardrail,
            mode="anthropic" if llm_was_used() else "offline", latency_ms=_ms(start),
        )


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)

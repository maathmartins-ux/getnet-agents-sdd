"""Escalation agent (FR-06, BR-05): opens a ticket and hands the conversation to a human.

Deterministic on purpose: a handoff must work even when the LLM is down.
"""
from __future__ import annotations

from ..schemas import AgentResult, RoutingDecision
from .base import call_tool

REASONS = {
    "human_request": ("você pediu atendimento humano", "you asked for a human agent"),
    "low_confidence": ("não tenho certeza suficiente para responder com segurança", "I'm not confident enough to answer safely"),
    "no_grounded_source": ("não encontrei essa informação no conteúdo oficial da Getnet", "I couldn't find this in Getnet's official content"),
    "answer_not_in_sources": ("não encontrei essa informação no conteúdo oficial da Getnet", "I couldn't find this in Getnet's official content"),
    "unknown_customer": ("não localizei o seu cadastro", "I couldn't find your merchant account"),
    "crm_unavailable": ("o sistema de dados da sua conta está indisponível agora", "your account data system is unavailable right now"),
}


def run(summary: str, user_id: str, decision: RoutingDecision, reason: str) -> AgentResult:
    pt = decision.language == "pt"
    result, call = call_tool(
        "open_support_ticket", {"user_id": user_id, "category": f"{decision.intent}:{reason}", "summary": summary[:280]}
    )
    why = REASONS.get(reason, (reason, reason))[0 if pt else 1]
    if result["ok"]:
        ticket = result["data"]["ticket_id"]  # type: ignore[index]
        answer = (
            f"Como {why}, encaminhei sua conversa para um especialista humano da Getnet. Protocolo: {ticket}. "
            "O atendente recebe o histórico desta conversa, então você não precisa repetir nada."
            if pt else
            f"Since {why}, I've handed your conversation to a Getnet human specialist. Ticket: {ticket}. "
            "The agent receives this conversation's history, so you won't need to repeat yourself."
        )
        return AgentResult(agent="escalation", answer=answer, tool_calls=[call], needs_handoff=True,
                           handoff_reason=reason, facts={"ticket_id": ticket})
    answer = (
        f"Como {why}, preciso de um atendente humano, mas não consegui abrir o protocolo agora. "
        "Por favor, use os canais oficiais de atendimento da Getnet."
        if pt else
        f"Since {why}, this needs a human agent, but I couldn't open a ticket right now. "
        "Please use Getnet's official support channels."
    )
    return AgentResult(agent="escalation", answer=answer, tool_calls=[call], needs_handoff=True, handoff_reason=reason)

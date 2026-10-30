"""Orchestrator: workflow between agents, handoff, fallback and response contract (FR-06, FR-07, FR-10, BR-01)."""
from typing import Any

import pytest

from getnet_agents.agents import knowledge
from getnet_agents.llm import LLMError, get_llm
from getnet_agents.orchestrator import Orchestrator
from getnet_agents.schemas import AgentResult, ChatRequest


def ask(message: str, user_id: str = "cliente1988", orc: Orchestrator | None = None):
    return (orc or Orchestrator()).handle(ChatRequest(message=message, user_id=user_id))


def test_offline_is_default_without_key() -> None:
    assert get_llm().mode == "offline"


def test_anthropic_without_key_stays_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert get_llm().mode == "offline"


def test_settlement_answer_uses_crm_numbers() -> None:
    r = ask("When will the money from yesterday's sales be deposited?")
    assert r.route == ["router", "support"]
    assert [t.name for t in r.tool_calls] == ["get_settlement_forecast"]
    assert r.tool_calls[0].args["user_id"] == "cliente1988"
    assert "2026-10-30" in r.answer and "R$" in r.answer
    assert r.language == "en" and r.mode == "offline" and r.handoff is None


def test_support_facts_flow_into_knowledge() -> None:
    r = ask("My card machine won't connect to the internet, what should I do?")
    assert r.route == ["router", "support", "knowledge"]
    assert r.tool_calls[0].name == "get_terminal_status"
    assert r.sources and all(s.url.startswith("https://") for s in r.sources)
    assert "According to Getnet's official content" in r.answer


def test_unknown_user_is_escalated_without_leaking_data() -> None:
    """BR-03: an unknown merchant gets a human handoff; the CRM refuses a ticket for a non-existent account,
    so the answer points to the official channels instead of inventing a protocol number."""
    r = ask("When will the money from yesterday's sales be deposited?", user_id="ghost")
    assert r.route == ["router", "support", "escalation"]
    assert r.handoff and r.handoff.required and r.handoff.reason == "unknown_customer"
    assert r.handoff.ticket_id is None
    assert "R$" not in r.answer and "official support channels" in r.answer


def test_known_user_handoff_has_ticket() -> None:
    r = ask("I want to talk to a human")
    assert r.route == ["router", "escalation"]
    assert r.handoff and r.handoff.ticket_id and r.handoff.ticket_id.startswith("GN-")


def test_no_source_no_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """BR-01: when retrieval finds nothing relevant, the answer is a handoff, never a guess."""
    monkeypatch.setenv("RETRIEVAL_MIN_SCORE", "0.999")
    r = ask("Can I sell through WhatsApp using the Payment Link?")
    assert r.route == ["router", "knowledge", "escalation"]
    assert r.handoff and r.handoff.reason == "no_grounded_source"
    assert r.sources == []
    assert "Ticket" in r.answer


def test_low_confidence_appends_escalation() -> None:
    r = ask("hmm ok")
    assert r.route[-1] == "escalation"
    assert r.handoff and r.handoff.reason in {"low_confidence", "no_grounded_source"}


def test_web_tool_disabled_degrades_honestly() -> None:
    r = ask("What's the euro exchange rate today?")
    assert r.route == ["router", "knowledge"]
    assert r.tool_calls[0].name == "get_exchange_rate" and r.tool_calls[0].ok is False
    assert r.handoff is None
    assert "couldn't retrieve" in r.answer


def test_web_tool_success_is_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    from getnet_agents.agents import base
    from getnet_agents.schemas import ToolCall

    def fake_call(name: str, args: dict[str, Any]):
        data = {"base": "EUR", "quote": "BRL", "bid": 6.0701, "ask": 6.0731, "high": 6.1, "low": 6.0,
                "create_date": "2026-10-30 14:56:00"}
        return ({"ok": True, "data": data, "source": "https://economia.awesomeapi.com.br/json/last/EUR-BRL",
                 "error": None}, ToolCall(name=name, args=args, ok=True, latency_ms=1.0))

    monkeypatch.setattr(knowledge, "call_tool", fake_call)
    r = ask("Qual a cotação do euro hoje?")
    assert r.language == "pt"
    assert "6.0701" in r.answer
    assert r.sources[0].url.startswith("https://economia.awesomeapi.com.br")
    assert base is not None


def test_pii_never_reaches_answer_or_tools() -> None:
    r = ask("Meu CPF é 123.456.789-09. Quando vou receber o dinheiro das vendas de ontem?")
    assert r.guardrail and r.guardrail.category == "pii_redacted" and not r.guardrail.blocked
    assert "123.456.789-09" not in r.answer
    assert all("123.456" not in str(t.args) for t in r.tool_calls)


class _FailingLLM:
    """Simulates Claude being down: every call raises after retries."""

    enabled = True
    mode = "anthropic"

    def complete_json(self, *a, **k):
        raise LLMError("down")

    def complete(self, *a, **k):
        raise LLMError("down")

    def tool_loop(self, *a, **k):
        raise LLMError("down")


def test_llm_outage_falls_back_to_offline_path() -> None:
    orc = Orchestrator(llm=_FailingLLM())
    for msg in ["Can I sell through WhatsApp using the Payment Link?",
                "When will the money from yesterday's sales be deposited?"]:
        r = ask(msg, orc=orc)
        assert r.answer and r.mode == "offline" and r.handoff is None


class _UngroundedLLM(_FailingLLM):
    def complete(self, *a, **k):
        return "Getnet gives free unlimited money to every merchant forever, guaranteed."


def test_ungrounded_llm_answer_is_replaced_by_extractive() -> None:
    r = ask("Can I sell through WhatsApp using the Payment Link?", orc=Orchestrator(llm=_UngroundedLLM()))
    assert "free unlimited money" not in r.answer
    assert r.answer.startswith("According to Getnet's official content:")


class _NotInSourcesLLM(_FailingLLM):
    def complete(self, *a, **k):
        return "NOT_IN_SOURCES"


def test_llm_not_in_sources_triggers_handoff() -> None:
    r = ask("Can I sell through WhatsApp using the Payment Link?", orc=Orchestrator(llm=_NotInSourcesLLM()))
    assert r.handoff and r.handoff.reason == "answer_not_in_sources"


def test_agent_result_contract() -> None:
    res = AgentResult(agent="knowledge")
    assert res.sources == [] and res.needs_handoff is False

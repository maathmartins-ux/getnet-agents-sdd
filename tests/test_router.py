"""Router agent: intent, language, plan and confidence (FR-02, BR-02, BR-04)."""
import pytest

from getnet_agents.agents.router import PLANS, classify_offline, detect_language, needs_escalation, route
from getnet_agents.llm import LLMError, OfflineLLM

CASE = [
    ("What's the difference between the Get Clássica and the Get Smart?", "product_info"),
    ("What's the weather forecast in Porto Alegre tomorrow?", "general_web"),
    ("When will the money from yesterday's sales be deposited?", "account_support"),
    ("Do I need a bank account to receive my sales via Pix?", "product_info"),
    ("My card machine won't connect to the internet, what should I do?", "technical_support"),
    ("How does receivables advance (antecipação) work with Getnet?", "product_info"),
    ("What's the euro exchange rate today?", "general_web"),
    ("My card machine is showing a transaction decline error.", "technical_support"),
    ("How many installments can I split a sale into with the crediário?", "product_info"),
    ("Can I sell through WhatsApp using the Payment Link?", "product_info"),
    ("Quero falar com um atendente", "human_request"),
]


@pytest.mark.parametrize("message,intent", CASE)
def test_case_intents(message: str, intent: str) -> None:
    d = classify_offline(message)
    assert d.intent == intent
    assert d.plan == PLANS[intent]
    assert d.plan[0] == "router"
    assert 0.45 <= d.confidence <= 1.0


@pytest.mark.parametrize(
    "text,lang",
    [
        ("How does receivables advance (antecipação) work with Getnet?", "en"),
        ("write me a poem about cats", "en"),
        ("Quando vou receber o dinheiro das vendas de ontem?", "pt"),
        ("Minha maquininha não conecta na internet", "pt"),
        ("Antecipação?", "pt"),
    ],
)
def test_language_follows_question(text: str, lang: str) -> None:
    assert detect_language(text) == lang


def test_no_cue_gives_low_confidence_and_escalation() -> None:
    d = classify_offline("hmm ok")
    assert d.confidence < 0.45
    assert needs_escalation(d)


def test_route_without_llm_is_the_rule_classifier() -> None:
    msg = "When will the money from yesterday's sales be deposited?"
    assert route(msg, OfflineLLM()) == classify_offline(msg)


class _BrokenLLM:
    enabled = True
    mode = "anthropic"

    def complete_json(self, *a, **k):
        raise LLMError("timeout")


class _WeirdLLM(_BrokenLLM):
    def complete_json(self, *a, **k):
        return {"intent": "teleport", "language": "xx"}


class _GoodLLM(_BrokenLLM):
    def complete_json(self, *a, **k):
        return {"intent": "technical_support", "language": "en", "confidence": 0.93, "rationale": "error"}


def test_llm_failure_falls_back_to_rules() -> None:
    assert route("Can I sell through WhatsApp using the Payment Link?", _BrokenLLM()).intent == "product_info"


def test_llm_invalid_intent_falls_back_to_rules() -> None:
    assert route("What's the euro exchange rate today?", _WeirdLLM()).intent == "general_web"


def test_llm_decision_keeps_code_defined_plan() -> None:
    d = route("anything", _GoodLLM())
    assert d.intent == "technical_support"
    assert d.plan == ["router", "support", "knowledge"]
    assert d.confidence == 0.93

"""Router agent (FR-02, BR-02, BR-04): intent, language, agent plan and confidence.

The *plan* is always derived from the intent in code (not by the LLM): routing stays predictable and
testable even when Claude classifies the message.
"""
from __future__ import annotations

import re

from ..config import get_settings
from ..guardrails import fold
from ..llm import AnthropicLLM, LLMError, OfflineLLM, load_prompt
from ..schemas import INTENTS, RoutingDecision

PLANS: dict[str, list[str]] = {
    "product_info": ["router", "knowledge"],
    "general_web": ["router", "knowledge"],
    "account_support": ["router", "support"],
    "technical_support": ["router", "support", "knowledge"],
    "human_request": ["router", "escalation"],
    "out_of_scope": ["router"],
    "unsafe": ["router"],
}

# (regex over accent-folded lowercase text, weight). Weights encode how specific a cue is.
RULES: dict[str, list[tuple[str, float]]] = {
    "human_request": [
        (r"\b(atendente|humano|human being|real person|pessoa de verdade|operador|operator|representative|supervisor|ouvidoria)\b", 4),
        (r"(talk|speak|chat) (to|with) (a |an )?(human|person|agent|someone)|falar com (um|uma|alguem)", 4),
    ],
    "account_support": [
        (r"\b(when will|quando (vou|vai|cai|caira|sera)|quando o dinheiro)\b", 2.5),
        (r"\b(deposit\w*|depositad\w*|settle\w*|liquida\w*|repasse|cair na conta|cai na (minha )?conta)\b", 2.5),
        (r"\b(my money|meu dinheiro|my balance|meu saldo|extrato|statement|my transactions|minhas transac\w*|minhas vendas|my sales history)\b", 2),
        (r"\b(yesterday|ontem|last week|semana passada)\b", 1),
        (r"\b(my (account|profile|plan)|minha conta|meu cadastro|meu plano)\b", 1.5),
    ],
    "technical_support": [
        (r"(won.?t|doesn.?t|does not|can.?t|cannot|not) (connect|work|turn on|print)|nao (conecta|liga|funciona|imprime)|sem (internet|conexao|sinal)", 3.5),
        (r"\b(declin\w*|recusad\w*|negad\w*|not authori[sz]ed|nao autorizad\w*)\b", 3),
        (r"\b(error|erro|falha|failure|offline|travad\w*|frozen|bug)\b", 2),
        (r"\b(internet|wi-?fi|chip|signal|sinal|bateria|battery|bobina|reiniciar|restart|reboot)\b", 1),
        (r"\b(my (card )?machine|minha maquin\w*|my terminal|meu terminal|my pos)\b", 1),
    ],
    "general_web": [
        (r"\b(weather|forecast|previsao do tempo|clima|temperatur\w*|vai chover|rain)\b", 4),
        (r"\b(exchange rate|cotacao|cambio|currency)\b", 4),
        (r"\b(euro|dolar|dollar|libra|pound|bitcoin)\b", 1.5),
        (r"\b(news|noticia\w*|search the web|pesquis\w* na (web|internet))\b", 2.5),
    ],
    "product_info": [
        (r"\b(getnet|get classica|get smart|get mini|get pos|superget)\b", 1.5),
        (r"\b(pix|antecipa\w*|receivables?|advance|crediario|installments?|parcel\w*|payment link|link de pagamento|whatsapp|tap to pay|tap on phone)\b", 2.5),
        (r"\b(bank account|conta bancaria|fees?|taxas?|rates?|plans?|planos?|tarifa\w*)\b", 1.5),
        (r"\b(difference|diferenca|compare|comparar|how does|como funciona|what is|o que e|do i need|preciso de|can i|posso)\b", 1),
        (r"\b(card machine|maquininha|maquina de cartao|product|produto)\b", 0.8),
    ],
}
_COMPILED = {intent: [(re.compile(p), w) for p, w in rules] for intent, rules in RULES.items()}

# Single letters ("a", "o") are excluded: "a" is also the English article.
_PT = re.compile(
    r"\b(os|as|de|do|da|dos|das|que|nao|com|para|uma|um|minha|meu|como|quando|qual|vou|preciso|posso|voce|ontem"
    r"|amanha|hoje|esta|maquininha|vendas|dinheiro|eu|no|na|em|por|sobre|quero|falar|funciona|tem|ser)\b"
)
_EN = re.compile(
    r"\b(the|is|are|what|when|how|my|can|do|does|will|i|you|to|of|and|with|from|yesterday|tomorrow|today"
    r"|machine|sales|money|me|about|please|write|tell|an|for|it|this|that|your|have|need|there|which|want)\b"
)


def detect_language(text: str) -> str:
    """pt/en by function-word evidence. Accents add a single point only: a Portuguese product name inside an
    English sentence ("receivables advance (antecipação)") must not flip the language (BR-02)."""
    folded = fold(text)
    pt = len(_PT.findall(folded)) + (1 if re.search(r"[ãõçáéíóúâêô]", text.lower()) else 0)
    en = len(_EN.findall(folded))
    return "en" if en > pt else "pt"


def _scores(text: str) -> dict[str, float]:
    folded = fold(text)
    return {i: sum(w for rx, w in rules if rx.search(folded)) for i, rules in _COMPILED.items()}


def classify_offline(text: str) -> RoutingDecision:
    lang = detect_language(text)
    scores = _scores(text)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (top, s1), (_, s2) = ranked[0], ranked[1]
    if s1 == 0:
        return RoutingDecision(intent="product_info", language=lang, plan=PLANS["product_info"], confidence=0.3,
                               rationale="no routing cue matched")
    # Account question that also names a product (e.g. "receive my sales via Pix") → product wins on tie.
    if top == "account_support" and scores["product_info"] >= s1 and not re.search(r"when will|quando", fold(text)):
        top, s1, s2 = "product_info", scores["product_info"], s1
    confidence = round(min(0.97, 0.5 + 0.45 * (s1 - s2) / (s1 + 1)), 3)
    if top in {"human_request", "general_web"}:
        confidence = max(confidence, 0.8)
    return RoutingDecision(intent=top, language=lang, plan=PLANS[top], confidence=confidence,
                           rationale=f"rule scores {dict(ranked[:3])}")


def route(text: str, llm: OfflineLLM | AnthropicLLM) -> RoutingDecision:
    """Classify with Claude (Haiku) when available; any LLM failure falls back to the rule classifier."""
    offline = classify_offline(text)
    if not llm.enabled:
        return offline
    try:
        data = llm.complete_json(load_prompt("router"), f"<message>\n{text}\n</message>", role="router")
        intent = data.get("intent")
        lang = data.get("language") if data.get("language") in {"pt", "en"} else offline.language
        if intent not in INTENTS:
            raise LLMError(f"unknown intent {intent!r}")
        conf = float(data.get("confidence", 0.5))
        return RoutingDecision(intent=intent, language=lang, plan=PLANS[intent], confidence=max(0.0, min(conf, 1.0)),
                               rationale=str(data.get("rationale", ""))[:300])
    except (LLMError, ValueError, TypeError):
        return offline


def needs_escalation(decision: RoutingDecision) -> bool:
    """BR-04: low-confidence routing is handed to a human in addition to the planned agents."""
    return decision.confidence < get_settings().router_min_confidence and decision.intent not in {"out_of_scope", "unsafe"}

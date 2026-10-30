"""Knowledge agent (FR-03, FR-04, BR-01, BR-08).

- Product questions: RAG over the Getnet corpus (retrieve → generate → grounding check). No relevant
  source means no answer: the agent asks for a handoff instead of guessing.
- General questions: live web tools (weather, exchange rate, web search).
"""
from __future__ import annotations

import re
from typing import Any

from ..config import get_settings
from ..guardrails import fold, is_grounded
from ..llm import AnthropicLLM, LLMError, OfflineLLM, load_prompt
from ..rag.store import Hit, load_store, tokenize
from ..schemas import AgentResult, RoutingDecision, Source, ToolCall
from .base import call_tool, fmt_date

CURRENCIES = {
    "euro": "EUR", "eur": "EUR", "dolar": "USD", "dollar": "USD", "usd": "USD", "libra": "GBP", "pound": "GBP",
    "gbp": "GBP", "peso": "ARS", "iene": "JPY", "yen": "JPY", "bitcoin": "BTC", "btc": "BTC",
}


# --------------------------------------------------------------------------- general (web tools)


def _city(text: str) -> str | None:
    m = re.search(r"\b(?:in|em|no|na|para|for)\s+([A-ZÀ-Ú][\wÀ-ú'-]+(?:\s+(?:[A-ZÀ-Ú][\wÀ-ú'-]+|do|da|de|dos|das))*)", text)
    return m.group(1).strip() if m else None


def _general(message: str, lang: str) -> AgentResult:
    folded = fold(message)
    calls: list[ToolCall] = []
    if re.search(r"weather|forecast|previsao|clima|tempo|temperatur|chuv|chov|rain", folded):
        city = _city(message) or "São Paulo"
        days = 0 if re.search(r"\b(today|hoje|now|agora)\b", folded) else 1
        result, call = call_tool("get_weather_forecast", {"city": city, "days_ahead": days})
        calls.append(call)
        if result["ok"]:
            d: dict[str, Any] = result["data"]  # type: ignore[assignment]
            desc = d.get(f"description_{lang}") or d.get("description_en", "")
            rain = d.get("precipitation_probability_max")
            place = ", ".join(x for x in (d.get("city"), d.get("admin1")) if x)
            if lang == "pt":
                answer = (f"Previsão para {place} em {fmt_date(d['date'], 'pt')}: {desc}, mínima de "
                          f"{d['temp_min_c']} °C e máxima de {d['temp_max_c']} °C"
                          + (f", com {rain}% de chance de chuva." if rain is not None else "."))
            else:
                answer = (f"Forecast for {place} on {fmt_date(d['date'], 'en')}: {desc}, low of "
                          f"{d['temp_min_c']} °C and high of {d['temp_max_c']} °C"
                          + (f", with a {rain}% chance of rain." if rain is not None else "."))
            return _web_ok(answer, "Open-Meteo", result["source"], calls)
        return _web_fail(lang, calls, result.get("error"))
    if re.search(r"exchange|cambio|cotacao|currency|euro|dolar|dollar|libra|pound", folded):
        base = next((code for word, code in CURRENCIES.items() if re.search(rf"\b{word}\b", folded)), "EUR")
        result, call = call_tool("get_exchange_rate", {"base": base, "quote": "BRL"})
        calls.append(call)
        if result["ok"]:
            d = result["data"]  # type: ignore[assignment]
            when = d.get("create_date") or ""
            if lang == "pt":
                answer = (f"Cotação {base}/BRL agora: compra R$ {d['bid']:.4f} e venda R$ {d['ask']:.4f} "
                          f"(máx. {d['high']:.4f}, mín. {d['low']:.4f}; atualizado em {when}).")
            else:
                answer = (f"{base}/BRL right now: bid R$ {d['bid']:.4f}, ask R$ {d['ask']:.4f} "
                          f"(high {d['high']:.4f}, low {d['low']:.4f}; updated {when}).")
            return _web_ok(answer, "AwesomeAPI", result["source"], calls)
        return _web_fail(lang, calls, result.get("error"))
    result, call = call_tool("web_search", {"query": message, "k": 3})
    calls.append(call)
    items = result["data"].get("results", []) if result["ok"] and isinstance(result["data"], dict) else []
    if items:
        head = "Encontrei na web:" if lang == "pt" else "From the web:"
        lines = [f"- {r['title']}: {r.get('snippet', '')}".strip() for r in items[:3]]
        sources = [Source(title=r["title"], url=r["url"], score=1.0) for r in items[:3]]
        return AgentResult(agent="knowledge", answer="\n".join([head, *lines]), sources=sources,
                           tool_calls=calls, grounded=True)
    return _web_fail(lang, calls, result.get("error"))


def _web_ok(answer: str, title: str, url: str, calls: list[ToolCall]) -> AgentResult:
    return AgentResult(agent="knowledge", answer=answer, sources=[Source(title=title, url=url, score=1.0)],
                       tool_calls=calls, grounded=True)


def _web_fail(lang: str, calls: list[ToolCall], error: str | None) -> AgentResult:
    """BR-08: honest degradation, no invented number, no ticket (not a Getnet issue)."""
    answer = (
        "Não consegui consultar essa informação em tempo real agora (serviço externo indisponível). "
        "Tente novamente em alguns minutos."
        if lang == "pt"
        else "I couldn't retrieve that information in real time right now (external service unavailable). "
        "Please try again in a few minutes."
    )
    return AgentResult(agent="knowledge", answer=answer, tool_calls=calls, grounded=False,
                       facts={"web_error": error or "unknown"})


# --------------------------------------------------------------------------- product (RAG)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]


def extractive_answer(query: str, hits: list[Hit], lang: str, max_sentences: int = 4) -> str:
    """Offline generation: the most query-relevant sentences of the top passages, verbatim (always grounded)."""
    q = set(tokenize(query))
    scored: list[tuple[float, int, int, str]] = []
    for rank, hit in enumerate(hits[:3]):
        for pos, sent in enumerate(_sentences(hit.doc.text)):
            overlap = len(q & set(tokenize(sent)))
            scored.append((overlap - 0.5 * rank - 0.05 * pos, rank, pos, sent))
    best = sorted(scored, reverse=True)[:max_sentences]
    best.sort(key=lambda t: (t[1], t[2]))  # keep document order for readability
    body = " ".join(s for *_, s in best)
    refs = " ".join(f"[{i + 1}]" for i in range(min(len(hits), 3)))
    intro = "Segundo o conteúdo oficial da Getnet:" if lang == "pt" else "According to Getnet's official content:"
    return f"{intro} {body} {refs}".strip()


def _rag(message: str, lang: str, llm: OfflineLLM | AnthropicLLM, extra_query: str = "") -> AgentResult:
    settings = get_settings()
    store = load_store(str(settings.knowledge_path))
    query = f"{message} {extra_query}".strip()
    hits = [h for h in store.search(query, lang=lang, k=4) if h.norm >= settings.retrieval_min_score]
    if not hits:
        return AgentResult(agent="knowledge", grounded=False, needs_handoff=True, handoff_reason="no_grounded_source")
    sources = [Source(title=h.doc.title, url=h.doc.url, score=h.norm) for h in hits]
    passages = [h.doc.text for h in hits]
    answer = ""
    if llm.enabled:
        numbered = "\n\n".join(f"[{i + 1}] {h.doc.title} ({h.doc.url})\n{h.doc.text}" for i, h in enumerate(hits))
        prompt = f"<question lang=\"{lang}\">\n{query}\n</question>\n\n<passages>\n{numbered}\n</passages>"
        try:
            candidate = llm.complete(load_prompt("knowledge"), prompt, role="agent")
            if "NOT_IN_SOURCES" in candidate:
                return AgentResult(agent="knowledge", sources=sources, grounded=False, needs_handoff=True,
                                   handoff_reason="answer_not_in_sources")
            if is_grounded(candidate, passages, settings.grounding_threshold):
                answer = candidate
        except LLMError:
            answer = ""
    if not answer:  # offline path, LLM failure, or LLM answer that failed the grounding check
        answer = extractive_answer(query, hits, lang)
    return AgentResult(agent="knowledge", answer=answer, sources=sources, grounded=True)


def run(message: str, decision: RoutingDecision, llm: OfflineLLM | AnthropicLLM,
        facts: dict[str, Any] | None = None) -> AgentResult:
    if decision.intent == "general_web":
        return _general(message, decision.language)
    hint = ""
    facts = facts or {}
    if facts.get("offline_terminals"):
        hint = "internet connection Wi-Fi chip signal restart troubleshooting conexão sinal reiniciar"
    if facts.get("declines"):
        hint += " transaction declined decline code card issuer transação recusada negada emissor"
    return _rag(message, decision.language, llm, extra_query=hint)

"""Customer Support agent (FR-05, BR-03): resolves account questions with CRM tools.

- Anthropic: manual tool-use loop over the CRM tool specs; `user_id` is always overwritten with the
  caller's id, so the model can never read another merchant's data.
- Offline: a deterministic planner picks the tools from the message and templated answers render the data.
Facts found here (offline terminals, declined transactions) are handed to the Knowledge agent.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..guardrails import fold
from ..llm import AnthropicLLM, LLMError, OfflineLLM, load_prompt
from ..schemas import AgentResult, RoutingDecision, ToolCall, ToolResult
from ..tools import CRM_TOOL_NAMES, specs_for
from .base import brl, call_tool, fmt_date

LOOP_TOOLS = tuple(n for n in CRM_TOOL_NAMES if n != "open_support_ticket")  # tickets belong to Escalation

PRODUCT = {"debit": ("Débito", "Debit"), "credit": ("Crédito", "Credit"), "pix": ("Pix", "Pix")}
STATUS = {
    "settled": ("já creditado", "already credited"),
    "due_today": ("cai hoje", "arrives today"),
}
TERMINAL_STATE = {
    "offline": ("offline", "offline"),
    "degraded": ("com conexão instável", "on an unstable connection"),
}
# English rendering of the decline codes present in the CRM (the CRM stores reasons in Portuguese).
DECLINE_EN = {
    "05": "transaction not authorized by the card issuer",
    "51": "insufficient balance or credit limit on the buyer's card",
    "54": "card expired",
    "55": "wrong PIN",
    "57": "transaction not allowed for this card",
}


def plan_tools(message: str, intent: str) -> list[tuple[str, dict[str, Any]]]:
    folded = fold(message)
    steps: list[tuple[str, dict[str, Any]]] = []
    if re.search(r"deposit|settle|liquida|repasse|receb|receive|money|dinheiro|cair|cai na|when will|quando", folded):
        steps.append(("get_settlement_forecast", {"reference_date": None}))
    if re.search(r"connect|conect|internet|wi-?fi|chip|sinal|signal|offline|liga|turn on", folded):
        steps.append(("get_terminal_status", {}))
    if re.search(r"declin|recusad|negad|authori|autoriz|error|erro", folded) and not any(
        s[0] == "get_terminal_status" for s in steps
    ):
        steps.append(("get_recent_transactions", {"status": "declined", "limit": 5}))
    elif re.search(r"transac|transaction|extrato|statement|vendas|sales", folded) and not steps:
        steps.append(("get_recent_transactions", {"status": None, "limit": 10}))
    if not steps:
        steps.append(("get_customer_profile", {}))
    return steps


def _render(name: str, data: Any, lang: str) -> tuple[str, dict[str, Any]]:
    pt = lang == "pt"
    facts: dict[str, Any] = {}
    if name == "get_settlement_forecast":
        ref = fmt_date(data["reference_date"], lang)
        lines = [f"Vendas de {ref}:" if pt else f"Sales from {ref}:"]
        for it in data.get("items", []):
            label = PRODUCT.get(it["product"], (it["product"], it["product"]))[0 if pt else 1]
            status = STATUS.get(it["status"], ("previsto", "expected"))[0 if pt else 1]
            lines.append(
                f"- {label}: {brl(it['net_amount'])} líquido ({brl(it['gross_amount'])} bruto - taxa {brl(it['fee_amount'])}), "
                f"depósito em {fmt_date(it['expected_date'], lang)} ({status})."
                if pt else
                f"- {label}: {brl(it['net_amount'])} net ({brl(it['gross_amount'])} gross - fee {brl(it['fee_amount'])}), "
                f"deposited on {fmt_date(it['expected_date'], lang)} ({status})."
            )
        if not data.get("items"):
            lines.append("Não encontrei vendas nessa data." if pt else "I found no sales on that date.")
        net = sum(it["net_amount"] for it in data.get("items", []))
        if data.get("items"):
            plan = data.get("receivables_plan", "")
            lines.append(
                f"Total líquido: {brl(net)}. Seu plano de recebimento é {plan} (dias úteis)." if pt
                else f"Total net: {brl(net)}. Your receivables plan is {plan} (business days)."
            )
        facts["settlement_total_net"] = round(net, 2)
        return "\n".join(lines), facts
    if name == "get_terminal_status":
        offline = [t for t in data.get("terminals", []) if t.get("status") != "online"]
        facts["offline_terminals"] = offline
        if not offline:
            return ("Todas as suas maquininhas aparecem online no nosso sistema." if pt
                    else "All your card machines show as online in our system."), facts
        parts = []
        for t in offline:
            seen = t.get("last_seen", "")[:16].replace("T", " ")
            state = TERMINAL_STATE.get(t.get("status", ""), (t.get("status", ""), t.get("status", "")))[0 if pt else 1]
            error = t.get("last_error") or "-"
            if t.get("active_link") and t.get("status") == "degraded":
                state += f" (operando pelo {t['active_link']})" if pt else f" (running on {t['active_link']})"
            parts.append(
                f"A maquininha {t['model']} ({t['terminal_id']}) está {state}; último contato em {seen}; erro registrado: \"{error}\"."
                if pt else
                f"Your {t['model']} ({t['terminal_id']}) is {state}; last seen {seen}; recorded error (as logged, in Portuguese): \"{error}\"."
            )
        return " ".join(parts), facts
    if name == "get_recent_transactions":
        rows = data if isinstance(data, list) else []
        declined = [r for r in rows if r.get("status") == "declined"]
        facts["declines"] = declined
        if not rows:
            return ("Não encontrei transações recentes." if pt else "I found no recent transactions."), facts
        if declined:
            head = "Encontrei estas recusas recentes:" if pt else "I found these recent declines:"
            lines = [head] + [
                f"- {r['datetime'][:16].replace('T', ' ')} · {r.get('brand', '')} {brl(r['amount'])}: código {r['decline_code']} ({r['decline_reason']})."
                if pt else
                f"- {r['datetime'][:16].replace('T', ' ')} · {r.get('brand', '')} {brl(r['amount'])}: code {r['decline_code']} "
                f"({DECLINE_EN.get(str(r['decline_code']), 'declined by the card issuer')})."
                for r in declined
            ]
            lines.append(
                "Esses códigos vêm do banco emissor do cartão do comprador, não indicam defeito na maquininha."
                if pt else
                "These codes are returned by the buyer's card issuer; they do not indicate a fault in your machine."
            )
            return "\n".join(lines), facts
        return (f"Você tem {len(rows)} transações recentes, todas aprovadas." if pt
                else f"You have {len(rows)} recent transactions, all approved."), facts
    if name == "get_customer_profile":
        return (f"Cadastro: {data.get('merchant_name')} ({data.get('city')}/{data.get('state')}), plano de recebimento "
                f"{data.get('receivables_plan')}, {data.get('terminal_count')} maquininha(s)." if pt
                else f"Account: {data.get('merchant_name')} ({data.get('city')}/{data.get('state')}), receivables plan "
                f"{data.get('receivables_plan')}, {data.get('terminal_count')} card machine(s)."), facts
    return json.dumps(data, ensure_ascii=False)[:500], facts


def _offline(message: str, user_id: str, decision: RoutingDecision) -> AgentResult:
    calls: list[ToolCall] = []
    parts: list[str] = []
    facts: dict[str, Any] = {}
    for name, args in plan_tools(message, decision.intent):
        result, call = call_tool(name, {"user_id": user_id, **{k: v for k, v in args.items() if v is not None}})
        calls.append(call)
        if not result["ok"]:
            if result.get("error", "").startswith("customer_not_found") or "not_found" in str(result.get("error")):
                return AgentResult(agent="support", tool_calls=calls, needs_handoff=True,
                                   handoff_reason="unknown_customer", facts={"tool_error": result.get("error")})
            return AgentResult(agent="support", tool_calls=calls, needs_handoff=True,
                               handoff_reason="crm_unavailable", facts={"tool_error": result.get("error")})
        text, f = _render(name, result["data"], decision.language)
        parts.append(text)
        facts.update(f)
    return AgentResult(agent="support", answer="\n".join(parts), tool_calls=calls, grounded=True, facts=facts)


def _with_llm(message: str, user_id: str, decision: RoutingDecision, llm: AnthropicLLM) -> AgentResult:
    facts: dict[str, Any] = {}
    records: list[ToolCall] = []

    def execute(name: str, args: dict[str, Any]) -> tuple[ToolResult, float]:
        if name not in LOOP_TOOLS:
            return {"ok": False, "data": {}, "source": "", "error": f"tool not allowed: {name}"}, 0.0
        args = {**args, "user_id": user_id}  # BR-03: never trust a model-supplied user_id
        result, call = call_tool(name, args)
        records.append(call)
        if result["ok"]:
            facts.update(_render(name, result["data"], decision.language)[1])
        return result, call.latency_ms

    user = f"<user_id>{user_id}</user_id>\n<language>{decision.language}</language>\n<message>\n{message}\n</message>"
    loop = llm.tool_loop(load_prompt("support"), user, specs_for(LOOP_TOOLS), execute)
    if not loop.text or not records:
        raise LLMError("support loop produced no grounded answer")
    return AgentResult(agent="support", answer=loop.text, tool_calls=records, grounded=True, facts=facts)


def run(message: str, user_id: str, decision: RoutingDecision, llm: OfflineLLM | AnthropicLLM) -> AgentResult:
    if llm.enabled:
        try:
            return _with_llm(message, user_id, decision, llm)  # type: ignore[arg-type]
        except LLMError:
            pass  # Article II: deterministic fallback
    return _offline(message, user_id, decision)

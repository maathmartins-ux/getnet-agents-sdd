"""Tool registry: Anthropic tool-use specs, handlers and a never-raising dispatcher."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from getnet_agents.schemas import ToolResult
from getnet_agents.tools import crm, web

ToolHandler = Callable[..., ToolResult]

_USER_ID = {
    "type": "string",
    "description": "Merchant identifier exactly as received in the request (e.g. 'cliente1988').",
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_customer_profile",
        "description": (
            "Returns the merchant's registration profile from the CRM: business name, masked "
            "document, city/state, segment, customer since, receivables plan (e.g. 'D+1' or "
            "'standard') and number of card machines. Use it to personalize support answers. "
            "Returns error 'customer_not_found' for unknown user_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"user_id": _USER_ID},
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_settlement_forecast",
        "description": (
            "Answers 'when will my money be deposited'. For the APPROVED sales of a reference date "
            "(default: yesterday), returns per product (debit, credit, pix) the gross amount, fee, "
            "net amount, expected deposit date, status (settled, due_today, scheduled) and the rule "
            "applied, plus totals and amounts grouped by expected date. Dates are in business days "
            "(weekends and Brazilian national holidays are skipped). Use the returned figures "
            "verbatim; never estimate dates yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": _USER_ID,
                "reference_date": {
                    "type": ["string", "null"],
                    "description": "Sales date in YYYY-MM-DD. Omit or null for yesterday.",
                },
            },
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_recent_transactions",
        "description": (
            "Lists the merchant's most recent card/Pix transactions, newest first, with amount, "
            "product, brand, terminal and status. Declined transactions include decline_code and "
            "decline_reason (issuer response). Use status='declined' to investigate 'transaction "
            "declined' complaints."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": _USER_ID,
                "status": {
                    "type": ["string", "null"],
                    "enum": ["approved", "declined", None],
                    "description": "Optional filter: 'approved' or 'declined'.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of transactions (default 10).",
                },
            },
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_terminal_status",
        "description": (
            "Returns each card machine (terminal) of the merchant: model (e.g. Get Smart, Get "
            "Clássica), serial, online/offline status, connectivity type (wifi/chip), signal, last "
            "time seen and last error. Use it first when the merchant reports connection problems."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"user_id": _USER_ID},
            "required": ["user_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_support_ticket",
        "description": (
            "Opens a support ticket for a human operator and returns its ticket_id. Idempotent: the "
            "same user_id, category and summary return the same ticket. Use only when the issue "
            "cannot be solved with the available data or the user asks for a human."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": _USER_ID,
                "category": {
                    "type": "string",
                    "description": "Short category, e.g. 'technical_support', 'account_support', 'human_request'.",
                },
                "summary": {
                    "type": "string",
                    "description": "One-paragraph summary of the problem, without card numbers or CPF.",
                },
            },
            "required": ["user_id", "category", "summary"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_weather_forecast",
        "description": (
            "Daily weather forecast for a city (Open-Meteo): min/max temperature in °C, rain "
            "probability, precipitation and a description in Portuguese and English. days_ahead: "
            "0=today, 1=tomorrow, up to 7. Brazilian cities are preferred for ambiguous names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. 'Porto Alegre'."},
                "days_ahead": {"type": "integer", "minimum": 0, "maximum": 7, "description": "Default 1 (tomorrow)."},
            },
            "required": ["city"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_exchange_rate",
        "description": (
            "Latest commercial exchange rate between two currencies (AwesomeAPI): bid, ask, daily "
            "high/low, percent change and quote timestamp. Defaults to EUR->BRL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base": {"type": "string", "description": "3-letter ISO code, default 'EUR'."},
                "quote": {"type": "string", "description": "3-letter ISO code, default 'BRL'."},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "web_search",
        "description": (
            "General-purpose web search (DuckDuckGo). Returns up to k results with title, url and "
            "snippet. Use only for questions that are not about Getnet products and are not covered "
            "by the weather or exchange-rate tools. Cite the result URLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Number of results (default 5)."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]

HANDLERS: dict[str, ToolHandler] = {
    "get_customer_profile": crm.get_customer_profile,
    "get_settlement_forecast": crm.get_settlement_forecast,
    "get_recent_transactions": crm.get_recent_transactions,
    "get_terminal_status": crm.get_terminal_status,
    "open_support_ticket": crm.open_support_ticket,
    "get_weather_forecast": web.get_weather_forecast,
    "get_exchange_rate": web.get_exchange_rate,
    "web_search": web.web_search,
}

CRM_TOOL_NAMES: tuple[str, ...] = (
    "get_customer_profile",
    "get_settlement_forecast",
    "get_recent_transactions",
    "get_terminal_status",
    "open_support_ticket",
)
WEB_TOOL_NAMES: tuple[str, ...] = ("get_weather_forecast", "get_exchange_rate", "web_search")

_SPECS_BY_NAME = {s["name"]: s for s in TOOL_SPECS}
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def specs_for(names: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """Subset of TOOL_SPECS, in the given order (e.g. specs_for(CRM_TOOL_NAMES))."""
    return [_SPECS_BY_NAME[n] for n in names]


def _validate(name: str, args: dict[str, Any]) -> str | None:
    schema = _SPECS_BY_NAME[name]["input_schema"]
    props: dict[str, Any] = schema["properties"]
    missing = [r for r in schema.get("required", []) if r not in args]
    if missing:
        return f"invalid_args: missing {missing}"
    unknown = [k for k in args if k not in props]
    if unknown:
        return f"invalid_args: unexpected {unknown}"
    for key, value in args.items():
        declared = props[key].get("type")
        types = declared if isinstance(declared, list) else [declared]
        allowed = tuple(t for d in types if d for t in _JSON_TYPES.get(d, (object,)))
        if isinstance(value, bool) and "boolean" not in types:
            return f"invalid_args: {key} has wrong type"
        if allowed and not isinstance(value, allowed):
            return f"invalid_args: {key} has wrong type"
    return None


def run_tool(name: str, args: dict[str, Any] | None) -> ToolResult:
    """Dispatch a tool call by name. Never raises: every failure is a ToolResult with ok=False."""
    source = f"tool://{name}"
    if name not in HANDLERS:
        return {"ok": False, "data": {}, "source": source, "error": f"unknown_tool: {name}"}
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {"ok": False, "data": {}, "source": source, "error": "invalid_args: args must be an object"}
    error = _validate(name, args)
    if error:
        return {"ok": False, "data": {}, "source": source, "error": error}
    try:
        return HANDLERS[name](**args)
    except Exception as exc:  # noqa: BLE001 - contract: tools never raise
        return {"ok": False, "data": {}, "source": source, "error": f"internal_error: {type(exc).__name__}: {exc}"}


__all__ = [
    "CRM_TOOL_NAMES",
    "HANDLERS",
    "TOOL_SPECS",
    "WEB_TOOL_NAMES",
    "run_tool",
    "specs_for",
]

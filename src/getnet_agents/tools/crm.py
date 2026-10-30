"""CRM tools used by the Customer Support agent.

Backed by a fictitious JSON dataset (data/crm/customers.json). Every function returns a
ToolResult and never raises: failures come back as ok=False with a machine-readable error.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from getnet_agents.schemas import ToolResult

_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "crm"
DEFAULT_CUSTOMERS_PATH = _DATA_DIR / "customers.json"
DEFAULT_HOLIDAYS_PATH = _DATA_DIR / "holidays.json"

TRANSACTION_STATUSES = ("approved", "declined")
CREDIT_STANDARD_DAYS = 30  # crédito à vista: D+30 corridos, ajustado para o próximo dia útil

# In-memory ticket store (no disk writes). Keyed by deterministic ticket id.
_TICKETS: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------- helpers
def _ok(data: dict[str, Any] | list[Any], source: str) -> ToolResult:
    return {"ok": True, "data": data, "source": source, "error": None}


def _fail(error: str, source: str) -> ToolResult:
    return {"ok": False, "data": {}, "source": source, "error": error}


def _safe(source_prefix: str) -> Callable[[Callable[..., ToolResult]], Callable[..., ToolResult]]:
    """Turn any unexpected exception into ok=False (tools never raise)."""

    def deco(fn: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> ToolResult:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - contract: tools never raise
                return _fail(f"internal_error: {type(exc).__name__}: {exc}", source_prefix)

        return wrapper

    return deco


def _customers_path() -> Path:
    return Path(os.getenv("CRM_DATA_PATH") or DEFAULT_CUSTOMERS_PATH)


def _holidays_path() -> Path:
    return Path(os.getenv("CRM_HOLIDAYS_PATH") or DEFAULT_HOLIDAYS_PATH)


@functools.lru_cache(maxsize=8)
def _load_json(path: str, mtime: float) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read(path: Path) -> dict[str, Any]:
    return _load_json(str(path), path.stat().st_mtime)


def _customer(user_id: str) -> dict[str, Any] | None:
    customers = _read(_customers_path()).get("customers", {})
    return customers.get(str(user_id).strip())


def _holidays() -> set[date]:
    path = _holidays_path()
    if not path.exists():
        return set()
    return {date.fromisoformat(h["date"]) for h in _read(path).get("holidays", [])}


def today() -> date:
    """'Today' for the CRM: env GETNET_TODAY (YYYY-MM-DD) for deterministic runs, else the system date."""
    raw = os.getenv("GETNET_TODAY")
    return date.fromisoformat(raw) if raw else date.today()


def is_business_day(d: date, holidays: set[date] | None = None) -> bool:
    hol = _holidays() if holidays is None else holidays
    return d.weekday() < 5 and d not in hol


def add_business_days(start: date, n: int, holidays: set[date] | None = None) -> date:
    """Return the date n business days after start (skips weekends and holidays)."""
    hol = _holidays() if holidays is None else holidays
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if is_business_day(d, hol):
            added += 1
    return d


def next_business_day_on_or_after(d: date, holidays: set[date] | None = None) -> date:
    hol = _holidays() if holidays is None else holidays
    while not is_business_day(d, hol):
        d += timedelta(days=1)
    return d


_PAN_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def mask_card(value: Any) -> Any:
    """Mask every card number (13-19 digits) in a value, keeping only the last 4 digits.

    Applied to everything a CRM tool returns, so a raw PAN in the data source can never leak.
    """
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            digits = re.sub(r"\D", "", m.group(0))
            return f"**** **** **** {digits[-4:]}" if 13 <= len(digits) <= 19 else m.group(0)

        return _PAN_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: mask_card(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_card(v) for v in value]
    return value


def _sanitize_transaction(tx: dict[str, Any]) -> dict[str, Any]:
    out = dict(tx)
    raw = out.pop("card_number", None)  # never expose a raw number, even if the source has one
    if raw and not out.get("card_masked"):
        digits = re.sub(r"\D", "", str(raw))
        out["card_masked"] = f"**** **** **** {digits[-4:]}" if digits else None
    return mask_card(out)


def _diagnose_terminal(t: dict[str, Any]) -> list[str]:
    """Machine-readable connectivity findings, e.g. ['wifi_weak', 'chip_ok']."""
    conn = t.get("connectivity")
    found: list[str] = []
    if isinstance(conn, dict):
        for link in ("wifi", "chip"):
            st = (conn.get(link) or {}).get("status")
            if st:
                found.append(f"{link}_{st}")
    elif conn:
        found.append(f"link_{conn}")
    if t.get("status") == "offline":
        found.append("terminal_offline")
    return found


def _money(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _settlement_rule(product: str, receivables_plan: str, ref: date, hol: set[date]) -> tuple[date, str]:
    if product == "pix":
        return ref, "Pix: crédito instantâneo na conta no momento da venda (D+0)."
    if product == "debit":
        return add_business_days(ref, 1, hol), "Débito: D+1 dia útil após a venda."
    if product == "credit":
        if receivables_plan.upper() == "D+1":
            return (
                add_business_days(ref, 1, hol),
                "Crédito com plano de recebimento D+1 contratado: D+1 dia útil após a venda.",
            )
        due = next_business_day_on_or_after(ref + timedelta(days=CREDIT_STANDARD_DAYS), hol)
        return due, "Crédito à vista: D+30 dias corridos, ajustado para o próximo dia útil."
    raise ValueError(f"unknown product: {product}")


def _status_for(expected: date, now: date) -> str:
    if expected < now:
        return "settled"
    if expected == now:
        return "due_today"
    return "scheduled"


# ---------------------------------------------------------------- tools
@_safe("crm://customers")
def get_customer_profile(user_id: str) -> ToolResult:
    """Merchant registration data (name, segment, city, receivables plan, terminal count)."""
    source = f"crm://customers/{user_id}"
    c = _customer(user_id)
    if c is None:
        return _fail("customer_not_found", source)
    data = {
        "user_id": c["user_id"],
        "merchant_name": c["merchant_name"],
        "document_masked": c.get("document_masked"),
        "city": c.get("city"),
        "state": c.get("state"),
        "segment": c.get("segment"),
        "since": c.get("since"),
        "receivables_plan": c.get("receivables_plan", "standard"),
        "terminal_count": len(c.get("terminals", [])),
        "fictitious": bool(c.get("fictitious", True)),
    }
    return _ok(data, source)


@_safe("crm://settlements")
def get_settlement_forecast(user_id: str, reference_date: str | None = None) -> ToolResult:
    """When the approved sales of reference_date (default: yesterday) will be deposited, in business days."""
    source = f"crm://settlements/{user_id}"
    c = _customer(user_id)
    if c is None:
        return _fail("customer_not_found", source)
    now = today()
    if reference_date:
        try:
            ref = date.fromisoformat(str(reference_date))
        except ValueError:
            return _fail("invalid_reference_date: expected YYYY-MM-DD", source)
    else:
        ref = now - timedelta(days=1)

    hol = _holidays()
    plan = str(c.get("receivables_plan", "standard"))
    fees = c.get("fees", {})
    groups: dict[str, dict[str, Any]] = {}
    for tx in c.get("transactions", []):
        if tx.get("status") != "approved":
            continue
        if datetime.fromisoformat(tx["datetime"]).date() != ref:
            continue
        product = tx["product"]
        g = groups.setdefault(product, {"gross": Decimal("0"), "count": 0})
        g["gross"] += Decimal(str(tx["amount"]))
        g["count"] += 1

    items: list[dict[str, Any]] = []
    by_date: dict[date, Decimal] = {}
    tot_gross = tot_fee = Decimal("0")
    for product in ("debit", "credit", "pix"):
        if product not in groups:
            continue
        g = groups[product]
        rate = Decimal(str(fees.get(product, 0)))
        gross: Decimal = g["gross"]
        fee = (gross * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        net = gross - fee
        expected, rule = _settlement_rule(product, plan, ref, hol)
        items.append(
            {
                "product": product,
                "transactions": g["count"],
                "gross_amount": _money(gross),
                "fee_rate": float(rate),
                "fee_amount": _money(fee),
                "net_amount": _money(net),
                "expected_date": expected.isoformat(),
                "status": _status_for(expected, now),
                "rule": rule,
            }
        )
        by_date[expected] = by_date.get(expected, Decimal("0")) + net
        tot_gross += gross
        tot_fee += fee

    data = {
        "user_id": c["user_id"],
        "reference_date": ref.isoformat(),
        "today": now.isoformat(),
        "receivables_plan": plan,
        "items": items,
        "by_date": [
            {"expected_date": d.isoformat(), "net_amount": _money(v), "status": _status_for(d, now)}
            for d, v in sorted(by_date.items())
        ],
        "total_gross": _money(tot_gross),
        "total_fee": _money(tot_fee),
        "total_net": _money(tot_gross - tot_fee),
        "business_day_rule": "Dias úteis excluem sábados, domingos e feriados nacionais.",
        "message": "" if items else "Nenhuma venda aprovada na data de referência.",
    }
    return _ok(data, source)


@_safe("crm://transactions")
def get_recent_transactions(user_id: str, status: str | None = None, limit: int = 10) -> ToolResult:
    """Most recent transactions (newest first), optionally filtered by status (approved|declined)."""
    source = f"crm://transactions/{user_id}"
    if status is not None and status not in TRANSACTION_STATUSES:
        return _fail(f"invalid_status: expected one of {list(TRANSACTION_STATUSES)}", source)
    c = _customer(user_id)
    if c is None:
        return _fail("customer_not_found", source)
    try:
        n = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        return _fail("invalid_limit", source)
    txs = [t for t in c.get("transactions", []) if status is None or t.get("status") == status]
    txs.sort(key=lambda t: t["datetime"], reverse=True)
    return _ok([_sanitize_transaction(t) for t in txs[:n]], source)


@_safe("crm://terminals")
def get_terminal_status(user_id: str) -> ToolResult:
    """Status of each card machine of the merchant (online/offline, connectivity, last error)."""
    source = f"crm://terminals/{user_id}"
    c = _customer(user_id)
    if c is None:
        return _fail("customer_not_found", source)
    terminals = []
    for t in c.get("terminals", []):
        item = dict(t)
        item["diagnosis"] = _diagnose_terminal(t)
        terminals.append(item)
    data = {
        "user_id": c["user_id"],
        "terminals": terminals,
        # "offline_count" = machines that are not fully online (offline or degraded): kept for callers.
        "offline_count": sum(1 for t in terminals if t.get("status") != "online"),
        "degraded_count": sum(1 for t in terminals if t.get("status") == "degraded"),
    }
    return _ok(data, source)


@_safe("crm://tickets")
def open_support_ticket(user_id: str, category: str, summary: str) -> ToolResult:
    """Open (idempotently) a support ticket; same inputs produce the same ticket id."""
    source = "crm://tickets"
    category = str(category or "").strip()
    summary = str(summary or "").strip()
    if not category or not summary:
        return _fail("invalid_args: category and summary are required", source)
    if _customer(user_id) is None:
        return _fail("customer_not_found", source)
    digest = hashlib.sha1(f"{user_id}|{category}|{summary}".encode()).hexdigest()[:8].upper()
    ticket_id = f"GN-{digest}"
    ticket = _TICKETS.get(ticket_id)
    if ticket is None:
        ticket = {
            "ticket_id": ticket_id,
            "user_id": user_id,
            "category": category,
            "summary": summary[:500],
            "status": "open",
            "created_on": today().isoformat(),
        }
        _TICKETS[ticket_id] = ticket
    return _ok(dict(ticket), f"crm://tickets/{ticket_id}")

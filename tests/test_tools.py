"""Tests for the CRM and web tools (Fork B). Zero real network: every HTTP call goes through MockTransport."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from getnet_agents.tools import CRM_TOOL_NAMES, HANDLERS, TOOL_SPECS, WEB_TOOL_NAMES, crm, run_tool, web


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=web.TIMEOUT_S)


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"real network is forbidden in tests: {request.url}")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GETNET_TODAY", "2026-10-30")
    monkeypatch.setenv("ENABLE_WEB_TOOLS", "true")
    monkeypatch.delenv("CRM_DATA_PATH", raising=False)
    web.set_client_factory(lambda: _client(_no_network))
    crm._TICKETS.clear()
    yield
    web.set_client_factory(None)


def _assert_tool_result(r: dict[str, Any]) -> None:
    assert set(r) == {"ok", "data", "source", "error"}
    assert isinstance(r["ok"], bool) and isinstance(r["source"], str)
    assert (r["error"] is None) == r["ok"]


# ------------------------------------------------------------------ registry
def test_registry_is_consistent() -> None:
    names = [s["name"] for s in TOOL_SPECS]
    assert sorted(names) == sorted(HANDLERS) == sorted(CRM_TOOL_NAMES + WEB_TOOL_NAMES)
    for spec in TOOL_SPECS:
        assert spec["description"] and spec["input_schema"]["type"] == "object"
        assert set(spec["input_schema"]["required"]) <= set(spec["input_schema"]["properties"])
    json.dumps(TOOL_SPECS)  # must be serializable for the Anthropic API


def test_run_tool_unknown_name_and_bad_args() -> None:
    r = run_tool("drop_database", {})
    _assert_tool_result(r)
    assert r["ok"] is False and r["error"].startswith("unknown_tool")
    assert run_tool("get_customer_profile", {})["error"].startswith("invalid_args: missing")
    assert run_tool("get_customer_profile", {"user_id": "cliente1988", "x": 1})["error"].startswith("invalid_args: unexpected")
    assert run_tool("get_recent_transactions", {"user_id": "cliente1988", "limit": "ten"})["error"].startswith("invalid_args")
    assert run_tool("get_customer_profile", "not-a-dict")["ok"] is False  # type: ignore[arg-type]


def test_run_tool_dispatches() -> None:
    r = run_tool("get_customer_profile", {"user_id": "cliente1988"})
    assert r["ok"] and r["data"]["merchant_name"] == "Padaria Bom Grão"


# ------------------------------------------------------------------ CRM
def test_customer_profile_and_unknown_user() -> None:
    r = crm.get_customer_profile("cliente1988")
    _assert_tool_result(r)
    assert r["data"]["receivables_plan"] == "D+1" and r["data"]["terminal_count"] == 2
    assert r["data"]["fictitious"] is True
    for fn in (crm.get_customer_profile, crm.get_settlement_forecast, crm.get_terminal_status, crm.get_recent_transactions):
        miss = fn("ghost")
        _assert_tool_result(miss)
        assert miss == {"ok": False, "data": {}, "source": miss["source"], "error": "customer_not_found"}


def test_settlement_forecast_yesterday_d1_plan() -> None:
    r = crm.get_settlement_forecast("cliente1988", None)
    _assert_tool_result(r)
    d = r["data"]
    assert d["reference_date"] == "2026-10-29" and d["today"] == "2026-10-30"
    items = {i["product"]: i for i in d["items"]}
    assert items["debit"] == {
        "product": "debit", "transactions": 2, "gross_amount": 165.90, "fee_rate": 0.0199,
        "fee_amount": 3.30, "net_amount": 162.60, "expected_date": "2026-10-30",
        "status": "due_today", "rule": "Débito: D+1 dia útil após a venda.",
    }
    assert items["credit"]["gross_amount"] == 801.80 and items["credit"]["net_amount"] == 773.82
    assert items["credit"]["expected_date"] == "2026-10-30" and "D+1" in items["credit"]["rule"]
    assert items["pix"]["expected_date"] == "2026-10-29" and items["pix"]["status"] == "settled"
    # totals reconcile line by line
    assert d["total_gross"] == round(165.90 + 801.80 + 75.00, 2) == 1042.70
    assert d["total_fee"] == round(3.30 + 27.98 + 0.0, 2) == 31.28
    assert d["total_net"] == round(162.60 + 773.82 + 75.00, 2) == 1011.42
    assert d["by_date"] == [
        {"expected_date": "2026-10-29", "net_amount": 75.00, "status": "settled"},
        {"expected_date": "2026-10-30", "net_amount": 936.42, "status": "due_today"},
    ]
    # declined sales never enter the forecast
    assert sum(i["transactions"] for i in d["items"]) == 5


def test_settlement_standard_credit_d30_adjusted_to_business_day() -> None:
    d = crm.get_settlement_forecast("cliente2045", "2026-10-29")["data"]
    items = {i["product"]: i for i in d["items"]}
    # 2026-10-29 + 30 days = 2026-11-28 (Saturday) -> Monday 2026-11-30
    assert items["credit"]["expected_date"] == "2026-11-30" and items["credit"]["status"] == "scheduled"
    assert items["debit"]["expected_date"] == "2026-10-30"


def test_d1_business_day_skips_weekend_and_finados(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hol = crm._holidays()
    assert date(2026, 11, 2) in hol and date(2026, 11, 20) in hol and date(2026, 12, 25) in hol
    assert crm.add_business_days(date(2026, 10, 23), 1, hol) == date(2026, 10, 26)  # Fri -> Mon
    assert crm.add_business_days(date(2026, 10, 30), 1, hol) == date(2026, 11, 3)  # Fri -> (Mon 02/11 Finados) -> Tue
    assert crm.add_business_days(date(2026, 11, 19), 1, hol) == date(2026, 11, 23)  # Thu -> (Fri 20/11) -> Mon
    data = {"customers": {"x": {
        "user_id": "x", "merchant_name": "X", "receivables_plan": "standard",
        "fees": {"debit": 0.01}, "terminals": [],
        "transactions": [{"id": "1", "datetime": "2026-10-30T10:00:00-03:00", "product": "debit",
                          "amount": 100.0, "status": "approved"}],
    }}}
    p = tmp_path / "customers.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("CRM_DATA_PATH", str(p))
    item = crm.get_settlement_forecast("x", "2026-10-30")["data"]["items"][0]
    assert item["expected_date"] == "2026-11-03" and item["net_amount"] == 99.0


def test_settlement_invalid_date_and_empty_day() -> None:
    assert crm.get_settlement_forecast("cliente1988", "29/10/2026")["error"].startswith("invalid_reference_date")
    d = crm.get_settlement_forecast("cliente1988", "2026-01-01")["data"]
    assert d["items"] == [] and d["total_net"] == 0.0 and d["message"]


def test_recent_transactions_filters_and_order() -> None:
    r = crm.get_recent_transactions("cliente1988", "declined")
    assert r["ok"] and [t["decline_code"] for t in r["data"]] == ["05", "51"]
    allt = crm.get_recent_transactions("cliente1988", None, limit=3)["data"]
    assert len(allt) == 3 and allt[0]["datetime"] >= allt[1]["datetime"] >= allt[2]["datetime"]
    assert crm.get_recent_transactions("cliente1988", "pending")["error"].startswith("invalid_status")
    assert crm.get_recent_transactions("cliente3101")["data"] == []


def test_terminal_status() -> None:
    d = crm.get_terminal_status("cliente1988")["data"]
    assert d["offline_count"] == 1
    offline = next(t for t in d["terminals"] if t["status"] == "offline")
    assert offline["model"] == "Get Clássica" and "Sem conexão" in offline["last_error"]


def test_ticket_is_deterministic_and_idempotent() -> None:
    a = crm.open_support_ticket("cliente1988", "technical_support", "Maquininha sem internet")
    b = crm.open_support_ticket("cliente1988", "technical_support", "Maquininha sem internet")
    c = crm.open_support_ticket("cliente1988", "technical_support", "Outro problema")
    assert a["ok"] and a["data"]["ticket_id"] == b["data"]["ticket_id"] != c["data"]["ticket_id"]
    assert a["data"]["ticket_id"].startswith("GN-") and len(a["data"]["ticket_id"]) == 11
    assert len(crm._TICKETS) == 2
    assert crm.open_support_ticket("cliente1988", "", "x")["error"].startswith("invalid_args")
    assert crm.open_support_ticket("ghost", "a", "b")["error"] == "customer_not_found"


def test_crm_internal_error_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("CRM_DATA_PATH", str(bad))
    r = crm.get_customer_profile("cliente1988")
    _assert_tool_result(r)
    assert r["ok"] is False and r["error"].startswith("internal_error")


# ------------------------------------------------------------------ web
GEO = {"results": [
    {"name": "Porto Alegre", "latitude": 38.97, "longitude": -8.0, "country_code": "PT", "country": "Portugal", "admin1": "Portalegre"},
    {"name": "Porto Alegre", "latitude": -30.03, "longitude": -51.23, "country_code": "BR", "country": "Brasil", "admin1": "Rio Grande do Sul"},
]}
FORECAST = {"daily": {
    "time": ["2026-10-30", "2026-10-31"], "weather_code": [3, 61],
    "temperature_2m_max": [24.1, 21.5], "temperature_2m_min": [15.0, 14.2],
    "precipitation_probability_max": [10, 80], "precipitation_sum": [0.0, 6.4],
}}


def test_weather_parses_and_prefers_brazil() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=GEO)
        assert req.url.params["latitude"] == "-30.03" and req.url.params["forecast_days"] == "2"
        return httpx.Response(200, json=FORECAST)

    r = web.get_weather_forecast("Porto Alegre", 1, client=_client(handler))
    _assert_tool_result(r)
    d = r["data"]
    assert d["country"] == "Brasil" and d["date"] == "2026-10-31"
    assert (d["temp_min_c"], d["temp_max_c"], d["precipitation_probability_max"]) == (14.2, 21.5, 80)
    assert d["description_pt"] == "chuva fraca" and d["description_en"] == "slight rain"
    assert r["source"].startswith(web.FORECAST_URL) and len(seen) == 2


def test_weather_errors() -> None:
    empty = _client(lambda req: httpx.Response(200, json={}))
    assert web.get_weather_forecast("Atlantis", client=empty)["error"] == "city_not_found"
    assert web.get_weather_forecast("", client=empty)["error"].startswith("invalid_args")
    assert web.get_weather_forecast("Recife", 9, client=empty)["error"].startswith("invalid_args")


def test_exchange_rate_parses() -> None:
    payload = {"EURBRL": {"code": "EUR", "codein": "BRL", "name": "Euro/Real Brasileiro", "high": "6.25",
                          "low": "6.18", "varBid": "0.01", "pctChange": "0.16", "bid": "6.2101",
                          "ask": "6.2131", "timestamp": "1793372400", "create_date": "2026-10-30 14:00:00"}}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/json/last/EUR-BRL"
        return httpx.Response(200, json=payload)

    r = web.get_exchange_rate(client=_client(handler))
    assert r["ok"] and r["data"]["bid"] == 6.2101 and r["data"]["ask"] == 6.2131
    assert r["source"] == "https://economia.awesomeapi.com.br/json/last/EUR-BRL"
    assert web.get_exchange_rate("EURO", "BRL")["error"].startswith("invalid_args")


DDG_HTML = """
<div class="result"><h2 class="result__title">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1&amp;rut=abc">First <b>Result</b></a></h2>
<a class="result__snippet" href="#">Snippet <b>one</b> &amp; more</a></div>
<div class="result"><a class="result__a" href="https://example.org/b">Second</a>
<div class="result__snippet">Snippet two</div></div>
"""


def test_web_search_parses_ddg_html() -> None:
    r = web.web_search("getnet", k=5, client=_client(lambda req: httpx.Response(200, text=DDG_HTML)))
    assert r["ok"]
    res = r["data"]["results"]
    assert res[0] == {"title": "First Result", "url": "https://example.com/a?x=1", "snippet": "Snippet one & more"}
    assert res[1] == {"title": "Second", "url": "https://example.org/b", "snippet": "Snippet two"}
    one = web.web_search("getnet", k=1, client=_client(lambda req: httpx.Response(200, text=DDG_HTML)))
    assert len(one["data"]["results"]) == 1
    assert web.web_search("  ")["error"].startswith("invalid_args")
    assert web.web_search("x", client=_client(lambda req: httpx.Response(202, text="")))["error"] == "http_202"


@pytest.mark.parametrize("name,args", [
    ("get_weather_forecast", {"city": "Porto Alegre"}),
    ("get_exchange_rate", {}),
    ("web_search", {"query": "getnet"}),
])
def test_web_tools_disabled(monkeypatch: pytest.MonkeyPatch, name: str, args: dict[str, Any]) -> None:
    monkeypatch.setenv("ENABLE_WEB_TOOLS", "false")
    r = run_tool(name, args)
    _assert_tool_result(r)
    assert r["ok"] is False and r["error"] == "disabled" and r["data"] == {}


@pytest.mark.parametrize("name,args", [
    ("get_weather_forecast", {"city": "Porto Alegre"}),
    ("get_exchange_rate", {}),
    ("web_search", {"query": "getnet"}),
])
def test_web_tools_timeout_and_http_error(name: str, args: dict[str, Any]) -> None:
    def timeout(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=req)

    web.set_client_factory(lambda: _client(timeout))
    assert run_tool(name, args)["error"] == "timeout"
    web.set_client_factory(lambda: _client(lambda req: httpx.Response(503)))
    assert run_tool(name, args)["error"] == "http_503"
    web.set_client_factory(lambda: _client(lambda req: httpx.Response(200, text="not json")))
    r = run_tool(name, args)
    assert r["ok"] is (name == "web_search")  # search parses HTML: no results but no crash
    if name != "web_search":
        assert r["error"].startswith("parse_error")


def test_client_factory_failure_never_raises() -> None:
    def boom() -> httpx.Client:
        raise RuntimeError("no client")

    web.set_client_factory(boom)
    r = web.get_exchange_rate()
    assert r["ok"] is False and r["error"].startswith("internal_error")

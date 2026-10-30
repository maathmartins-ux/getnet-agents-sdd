"""Acceptance tests: the 10 scenarios of the case through the HTTP API (spec.md §7, AC-01..AC-10).

Deterministic offline mode (no API key, no network): LLM_PROVIDER=offline, ENABLE_WEB_TOOLS=false,
GETNET_TODAY=2026-10-30. Expectations come from the `official` split of evals/dataset.jsonl, so tests and
the eval runner share one golden set. The remaining tests cover the dataset contract and the eval scoring.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # makes `evals` importable (pyproject only puts src/ on the path)

run_evals = importlib.import_module("evals.run_evals")

DATASET = ROOT / "evals" / "dataset.jsonl"
CASES = run_evals.load_dataset(DATASET)
OFFICIAL = [c for c in CASES if c["split"] == "official"]

# The 10 messages of the case, verbatim (AI Hardcore Engineer - Multi-Agent Support System.md).
CASE_MESSAGES = [
    "What's the difference between the Get Clássica and the Get Smart?",
    "What's the weather forecast in Porto Alegre tomorrow?",
    "When will the money from yesterday's sales be deposited?",
    "Do I need a bank account to receive my sales via Pix?",
    "My card machine won't connect to the internet, what should I do?",
    "How does receivables advance (antecipação) work with Getnet?",
    "What's the euro exchange rate today?",
    "My card machine is showing a transaction decline error.",
    "How many installments can I split a sale into with the crediário?",
    "Can I sell through WhatsApp using the Payment Link?",
]
SOURCES_REQUIRED = {"official-01", "official-04", "official-05", "official-06", "official-10"}  # AC-01/04/05/06/10


@pytest.fixture(scope="module")
def offline_env():
    with pytest.MonkeyPatch.context() as mp:
        for key, value in run_evals.OFFLINE_ENV.items():
            mp.setenv(key, value)
        yield


@pytest.fixture(scope="module")
def client(offline_env):
    from fastapi.testclient import TestClient

    from getnet_agents.api import app

    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- the 10 official scenarios


@pytest.mark.parametrize("case", OFFICIAL, ids=[c["id"] for c in OFFICIAL])
def test_official_scenario(client, case):
    r = client.post("/chat", json={"message": case["message"], "user_id": case["user_id"]})
    assert r.status_code < 500, r.text
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["intent"] == case["expected_intent"]
    assert body["route"] == case["expected_route"]
    assert body["language"] == case["expected_language"]
    assert body["mode"] == "offline"
    assert body["answer"].strip()
    assert body["trace_id"]
    assert 0.0 <= body["confidence"] <= 1.0

    called = {t["name"] for t in body["tool_calls"]}
    assert set(case["expected_tools"]) <= called, f"expected tools {case['expected_tools']}, called {sorted(called)}"
    if case["id"] in SOURCES_REQUIRED:
        assert body["sources"], "grounded answer must cite at least one source"
        assert all(s["url"].startswith("https://") for s in body["sources"])
    assert not (body["guardrail"] or {}).get("blocked")
    assert not (body["handoff"] or {}).get("required")


def test_http_rejects_invalid_payload(client):
    assert client.post("/chat", json={"message": "", "user_id": "cliente1988"}).status_code == 422
    assert client.post("/chat", json={"user_id": "cliente1988"}).status_code == 422
    assert client.post("/chat", json={"message": "x" * 2001, "user_id": "cliente1988"}).status_code == 422


def test_unknown_customer_never_crashes(client):
    r = client.post("/chat", json={"message": "When will yesterday's sales be deposited?", "user_id": "nao-existe"})
    assert r.status_code == 200, r.text
    assert r.json()["answer"].strip()


# --------------------------------------------------------------------------- dataset contract


def test_official_split_is_the_case_verbatim():
    assert [c["message"] for c in OFFICIAL] == CASE_MESSAGES
    assert {c["user_id"] for c in OFFICIAL} == {"cliente1988"}


def test_dataset_is_valid_and_has_enough_cases():
    assert run_evals.validate_dataset(CASES) == []
    by_split = {s: sum(1 for c in CASES if c["split"] == s) for s in run_evals.SPLITS}
    assert by_split["official"] == 10
    assert by_split["heldout"] >= 20
    assert by_split["guardrail"] >= 5
    assert any(c["expect_handoff"] for c in CASES) and any(c["expect_blocked"] for c in CASES)
    assert any(c.get("must_not_include") for c in CASES), "guardrail split must check for leaks"


def test_validate_dataset_catches_bad_rows():
    bad = [{"id": "x", "split": "nope"}, {**OFFICIAL[0], "id": "y", "expected_intent": "banana"}]
    problems = run_evals.validate_dataset(bad)
    assert any("missing fields" in p for p in problems)
    assert any("banana" in p for p in problems)


# --------------------------------------------------------------------------- eval scoring


def _response(**overrides):
    base = {"answer": "Débito: R$ 162,60", "intent": "account_support", "route": ["router", "support"],
            "language": "pt", "tool_calls": [{"name": "get_settlement_forecast"}], "guardrail": None, "handoff": None}
    return {**base, **overrides}


def _case(**overrides):
    base = {"id": "t", "split": "heldout", "message": "m", "user_id": "u", "expected_intent": "account_support",
            "expected_route": ["router", "support"], "expected_tools": ["get_settlement_forecast"],
            "expect_handoff": False, "expect_blocked": False, "expected_language": "pt",
            "must_include_any": ["R$"]}
    return {**base, **overrides}


def test_score_case_perfect_response():
    c = run_evals.score_case(_case(), _response())
    assert c["intent"] and c["plan"] and c["language"] and c["handoff"] and c["guardrail"] and c["keywords"]
    assert (c["tool_tp"], c["tool_fp"], c["tool_fn"]) == (1, 0, 0)


def test_score_case_tool_precision_and_recall():
    c = run_evals.score_case(_case(expected_tools=["get_settlement_forecast", "get_customer_profile"]),
                             _response(tool_calls=[{"name": "get_settlement_forecast"}, {"name": "web_search"}]))
    assert (c["tool_tp"], c["tool_fp"], c["tool_fn"]) == (1, 1, 1)


def test_score_case_flags_pii_leak_as_guardrail_failure():
    case = _case(must_not_include=["123.456.789-09"])
    leaked = run_evals.score_case(case, _response(answer="Seu CPF 123.456.789-09 R$ 1"))
    assert leaked["guardrail"] is False and leaked["leaked"] == ["123.456.789-09"]
    assert run_evals.score_case(case, _response())["guardrail"] is True


def test_score_case_keywords_ignore_accents_and_case():
    c = run_evals.score_case(_case(must_include_any=["classica"]), _response(answer="A Get CLÁSSICA é..."))
    assert c["keywords"] is True


def test_aggregate_counts_crashes_as_misses():
    ok = {"id": "a", "split": "heldout", "message": "m", "expected": {}, "got": {}, "error": None,
          "latency_ms": 10.0, "checks": run_evals.score_case(_case(), _response())}
    crash = {"id": "b", "split": "heldout", "message": "m", "expected": {}, "got": {}, "error": "boom",
             "latency_ms": 20.0, "checks": run_evals.score_case(_case(), None)}
    m = run_evals.aggregate([ok, crash])
    assert m["n"] == 2 and m["crashes"] == 1
    assert m["routing_accuracy"] == 0.5
    assert m["latency_p50_ms"] == 15.0


def test_gate_exit_codes(tmp_path, offline_env):
    good = [c for c in OFFICIAL if c["id"] == "official-01"]
    bad = [{**good[0], "id": "official-bad", "expected_intent": "account_support"}]
    ok_path, bad_path = tmp_path / "ok.jsonl", tmp_path / "bad.jsonl"
    ok_path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in good), encoding="utf-8")
    bad_path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in bad), encoding="utf-8")

    assert run_evals.main(["--dataset", str(ok_path), "--out", str(tmp_path / "r1")]) == 0
    assert run_evals.main(["--dataset", str(bad_path), "--out", str(tmp_path / "r2")]) == 1
    assert (tmp_path / "r1" / "latest.json").exists()
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text(json.dumps({"id": "x"}), encoding="utf-8")
    assert run_evals.main(["--dataset", str(invalid), "--out", str(tmp_path / "r3")]) == 2

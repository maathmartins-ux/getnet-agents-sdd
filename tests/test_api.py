"""HTTP API contract (FR-01, FR-10)."""
from fastapi.testclient import TestClient

from getnet_agents.api import app
from getnet_agents.schemas import ChatResponse

FIELDS = {"trace_id", "answer", "language", "route", "intent", "confidence", "sources", "tool_calls", "handoff",
          "guardrail", "mode", "latency_ms"}


def test_chat_returns_full_contract() -> None:
    with TestClient(app) as client:
        r = client.post("/chat", json={"message": "Can I sell through WhatsApp using the Payment Link?",
                                       "user_id": "cliente1988"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == FIELDS
    ChatResponse.model_validate(body)
    assert body["route"] == ["router", "knowledge"]
    assert len(body["trace_id"]) == 32


def test_validation_errors() -> None:
    with TestClient(app) as client:
        assert client.post("/chat", json={"message": "", "user_id": "x"}).status_code == 422
        assert client.post("/chat", json={"message": "x" * 2001, "user_id": "x"}).status_code == 422
        assert client.post("/chat", json={"message": "oi"}).status_code == 422


def test_health_and_metrics() -> None:
    with TestClient(app) as client:
        client.post("/chat", json={"message": "Quero falar com um atendente", "user_id": "cliente1988"})
        health = client.get("/health").json()
        metrics = client.get("/metrics").text
    assert health["status"] == "ok" and health["mode"] == "offline" and health["documents"] >= 20
    assert "getnet_requests_total" in metrics
    assert 'getnet_handoffs_total{reason="human_request"}' in metrics
    assert "getnet_span_duration_seconds_bucket" in metrics

"""Observability: JSON logs bound to trace_id, spans per agent, Prometheus exposition (NFR-04)."""
import json
import logging

import pytest

from getnet_agents.observability import _Registry, configure_logging, logger, new_trace, span
from getnet_agents.orchestrator import Orchestrator
from getnet_agents.schemas import ChatRequest


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def captured():
    configure_logging("INFO")
    from getnet_agents.observability import JsonFormatter

    h = _Capture()
    h.setFormatter(JsonFormatter())
    logger.addHandler(h)
    yield h.lines
    logger.removeHandler(h)


def test_every_span_carries_the_request_trace_id(captured: list[dict]) -> None:
    r = Orchestrator().handle(ChatRequest(message="My card machine is showing a transaction decline error.",
                                          user_id="cliente1988"))
    mine = [line for line in captured if line["trace_id"] == r.trace_id]
    agents = [line["agent"] for line in mine if line.get("span") == "agent"]
    assert agents == ["router", "support", "knowledge"]
    assert any(line.get("span") == "tool" and line["tool"] == "get_recent_transactions" for line in mine)
    done = [line for line in mine if line["event"] == "request_completed"]
    assert done and done[0]["route"] == ["router", "support", "knowledge"]


def test_logs_never_contain_raw_pii(captured: list[dict]) -> None:
    Orchestrator().handle(ChatRequest(message="cpf 123.456.789-09 quando recebo minhas vendas de ontem?",
                                      user_id="cliente1988"))
    assert "123.456.789-09" not in json.dumps(captured, ensure_ascii=False)


def test_span_records_errors(captured: list[dict]) -> None:
    new_trace()
    with pytest.raises(RuntimeError):
        with span("unit", step="x"):
            raise RuntimeError("boom")
    assert captured[-1]["status"] == "error" and captured[-1]["span"] == "unit"


def test_prometheus_rendering() -> None:
    reg = _Registry()
    reg.inc("c_total", "a counter", tool="x")
    reg.observe("h_seconds", "a histogram", 0.02, span="router")
    text = reg.render()
    assert '# TYPE c_total counter' in text and 'c_total{tool="x"} 1.0' in text
    assert 'h_seconds_bucket{span="router",le="0.025"} 1' in text
    assert 'h_seconds_count{span="router"} 1' in text

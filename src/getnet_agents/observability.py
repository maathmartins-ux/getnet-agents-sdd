"""Structured JSON logs, per-agent spans bound to a trace_id, and Prometheus metrics (no external deps).

Swap point: `span()` can be backed by OpenTelemetry without touching the agents.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")
_llm_used: ContextVar[bool] = ContextVar("llm_used", default=False)

logger = logging.getLogger("getnet_agents")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "trace_id": _trace_id.get(),
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    if any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        logger.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def new_trace() -> str:
    tid = uuid.uuid4().hex
    _trace_id.set(tid)
    _llm_used.set(False)
    return tid


def current_trace() -> str:
    return _trace_id.get()


def mark_llm_used() -> None:
    _llm_used.set(True)


def llm_was_used() -> bool:
    return _llm_used.get()


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, event, extra={"fields": fields})


# --------------------------------------------------------------------------- metrics

_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class _Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(lambda: defaultdict(float))
        self.hist: dict[str, dict[tuple[tuple[str, str], ...], list[float]]] = defaultdict(dict)
        self.help: dict[str, tuple[str, str]] = {}

    def inc(self, name: str, help_: str, value: float = 1.0, **labels: str) -> None:
        key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._lock:
            self.help.setdefault(name, ("counter", help_))
            self.counters[name][key] += value

    def observe(self, name: str, help_: str, value: float, **labels: str) -> None:
        key = tuple(sorted((k, str(v)) for k, v in labels.items()))
        with self._lock:
            self.help.setdefault(name, ("histogram", help_))
            series = self.hist[name].setdefault(key, [0.0] * (len(_BUCKETS) + 2))  # buckets.., sum, count
            for i, b in enumerate(_BUCKETS):
                if value <= b:
                    series[i] += 1
            series[-2] += value
            series[-1] += 1

    def render(self) -> str:
        def fmt(labels: tuple[tuple[str, str], ...], extra: dict[str, str] | None = None) -> str:
            items = list(labels) + list((extra or {}).items())
            if not items:
                return ""
            return "{" + ",".join(f'{k}="{v}"' for k, v in items) + "}"

        lines: list[str] = []
        with self._lock:
            for name, (kind, help_) in sorted(self.help.items()):
                lines.append(f"# HELP {name} {help_}")
                lines.append(f"# TYPE {name} {kind}")
                if kind == "counter":
                    for labels, v in self.counters[name].items():
                        lines.append(f"{name}{fmt(labels)} {v}")
                else:
                    for labels, s in self.hist[name].items():
                        for i, b in enumerate(_BUCKETS):
                            lines.append(f"{name}_bucket{fmt(labels, {'le': str(b)})} {s[i]}")
                        lines.append(f"{name}_bucket{fmt(labels, {'le': '+Inf'})} {s[-1]}")
                        lines.append(f"{name}_sum{fmt(labels)} {s[-2]}")
                        lines.append(f"{name}_count{fmt(labels)} {s[-1]}")
        return "\n".join(lines) + "\n"


REGISTRY = _Registry()


def inc(name: str, help_: str, value: float = 1.0, **labels: str) -> None:
    REGISTRY.inc(name, help_, value, **labels)


def observe(name: str, help_: str, value: float, **labels: str) -> None:
    REGISTRY.observe(name, help_, value, **labels)


def render_metrics() -> str:
    return REGISTRY.render()


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
    """Time a unit of work (an agent, a tool, an LLM call); emits one JSON log line and a histogram sample.

    The yielded dict can be enriched by the caller; its content goes into the log line.
    """
    info: dict[str, Any] = dict(attrs)
    start = time.perf_counter()
    status = "ok"
    try:
        yield info
    except Exception:
        status = "error"
        raise
    finally:
        ms = (time.perf_counter() - start) * 1000
        log_event("span", span=name, status=status, duration_ms=round(ms, 2), **info)
        observe("getnet_span_duration_seconds", "Duration of agent/tool/LLM spans", ms / 1000, span=name, status=status)

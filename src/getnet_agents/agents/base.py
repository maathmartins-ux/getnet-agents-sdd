"""Helpers shared by the specialist agents: instrumented tool execution."""
from __future__ import annotations

import time
from typing import Any

from ..observability import inc, span
from ..schemas import ToolCall, ToolResult
from ..tools import run_tool


def call_tool(name: str, args: dict[str, Any]) -> tuple[ToolResult, ToolCall]:
    """Run a tool through `run_tool` (never raises), with a span, a metric and a `ToolCall` record."""
    start = time.perf_counter()
    with span("tool", tool=name) as info:
        result = run_tool(name, args)
        info["ok"] = result["ok"]
        if result.get("error"):
            info["error"] = result["error"]
    latency = round((time.perf_counter() - start) * 1000, 2)
    inc("getnet_tool_calls_total", "Tool calls by tool and outcome", tool=name, ok=str(result["ok"]).lower())
    return result, ToolCall(name=name, args=args, ok=result["ok"], latency_ms=latency)


def brl(value: float) -> str:
    """R$ 1.011,42 formatting."""
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_date(iso: str, lang: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}" if lang == "pt" else f"{y}-{m}-{d}"

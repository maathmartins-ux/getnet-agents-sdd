"""Offline evaluation runner for the multi-agent orchestration.

Runs every case of a JSONL golden set through ``Orchestrator().handle`` and scores
routing, language, grounding, tool use, handoff and guardrails. Exit code 1 when the
routing gate (intent accuracy) falls below ``--min-routing`` — this is the CI gate.

Usage:
    python evals/run_evals.py                      # deterministic, offline
    python evals/run_evals.py --live               # uses LLM_PROVIDER / web tools from env
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals" / "dataset.jsonl"
DEFAULT_OUT_DIR = ROOT / "evals" / "results"

OFFLINE_ENV = {
    "LLM_PROVIDER": "offline",
    "ENABLE_WEB_TOOLS": "false",
    "GETNET_TODAY": "2026-10-30",
}


def load_dataset(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON ({exc})") from exc
    return rows


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


def ratio(hits: int, total: int) -> float:
    return hits / total if total else 1.0


def score_case(case: dict[str, Any], resp: dict[str, Any]) -> dict[str, Any]:
    """Compare one response against its expectations; returns per-check booleans."""
    guardrail = resp.get("guardrail") or {}
    handoff = resp.get("handoff") or {}
    called = {tc["name"] for tc in resp.get("tool_calls", [])}
    expected_tools = set(case.get("expected_tools", []))
    sources = resp.get("sources", [])

    checks: dict[str, Any] = {
        "intent": resp.get("intent") == case["expected_intent"],
        "route": resp.get("route") == case["expected_route"],
        "language": resp.get("language") == case["expected_language"],
        "tools_hit": len(expected_tools & called),
        "tools_expected": len(expected_tools),
        "blocked_pred": bool(guardrail.get("blocked")),
        "blocked_true": bool(case.get("expected_blocked")),
    }
    if case.get("must_have_sources"):
        checks["sources"] = bool(sources) and all(
            str(s.get("url", "")).startswith("https://") for s in sources
        )
    if case.get("expected_handoff") is not None:
        checks["handoff"] = bool(handoff.get("required")) == case["expected_handoff"]
    return checks


def run(dataset: Path, live: bool) -> dict[str, Any]:
    if not live:
        os.environ.update(OFFLINE_ENV)
    sys.path.insert(0, str(ROOT / "src"))
    # Imported after the environment is set so config picks up the offline values.
    from getnet_agents.orchestrator import Orchestrator
    from getnet_agents.schemas import ChatRequest

    orch = Orchestrator()
    rows = load_dataset(dataset)
    results = []
    for case in rows:
        t0 = time.perf_counter()
        try:
            resp = orch.handle(
                ChatRequest(message=case["message"], user_id=case["user_id"])
            ).model_dump()
            error = None
        except Exception as exc:  # an eval must report crashes, not stop on them
            resp, error = {}, f"{type(exc).__name__}: {exc}"
        elapsed_ms = (time.perf_counter() - t0) * 1000
        checks = score_case(case, resp) if error is None else {}
        results.append(
            {
                "id": case["id"],
                "message": case["message"],
                "expected": {
                    "intent": case["expected_intent"],
                    "route": case["expected_route"],
                    "language": case["expected_language"],
                },
                "got": {
                    "intent": resp.get("intent"),
                    "route": resp.get("route"),
                    "language": resp.get("language"),
                    "tools": [tc["name"] for tc in resp.get("tool_calls", [])],
                    "sources": len(resp.get("sources", [])),
                    "handoff": (resp.get("handoff") or {}).get("required"),
                    "guardrail": (resp.get("guardrail") or {}).get("category"),
                },
                "checks": checks,
                "error": error,
                "latency_ms": round(elapsed_ms, 2),
            }
        )
    return {"results": results, "metrics": aggregate(results), "live": live}


def aggregate(results: list[dict[str, Any]]) -> dict[str, float]:
    ok = [r for r in results if r["error"] is None]
    n = len(results)
    c = [r["checks"] for r in ok]
    tp = sum(1 for x in c if x["blocked_pred"] and x["blocked_true"])
    fp = sum(1 for x in c if x["blocked_pred"] and not x["blocked_true"])
    fn = sum(1 for x in c if not x["blocked_pred"] and x["blocked_true"])
    lat = [r["latency_ms"] for r in results]
    src = [x["sources"] for x in c if "sources" in x]
    hof = [x["handoff"] for x in c if "handoff" in x]
    return {
        "cases": n,
        "errors": n - len(ok),
        "intent_accuracy": ratio(sum(x["intent"] for x in c), n),
        "route_exact_match": ratio(sum(x["route"] for x in c), n),
        "language_accuracy": ratio(sum(x["language"] for x in c), n),
        "source_coverage": ratio(sum(src), len(src)),
        "tool_recall": ratio(
            sum(x["tools_hit"] for x in c), sum(x["tools_expected"] for x in c)
        ),
        "handoff_accuracy": ratio(sum(hof), len(hof)),
        "guardrail_precision": ratio(tp, tp + fp),
        "guardrail_recall": ratio(tp, tp + fn),
        "latency_p50_ms": round(statistics.median(lat), 2) if lat else 0.0,
        "latency_p95_ms": round(percentile(lat, 95), 2),
    }


def failures(results: list[dict[str, Any]]) -> list[str]:
    out = []
    for r in results:
        if r["error"]:
            out.append(f"{r['id']}: CRASH {r['error']}")
            continue
        bad = [
            k
            for k, v in r["checks"].items()
            if isinstance(v, bool) and not v and k not in ("blocked_pred", "blocked_true")
        ]
        if r["checks"]["tools_hit"] < r["checks"]["tools_expected"]:
            bad.append("tools")
        if r["checks"]["blocked_pred"] != r["checks"]["blocked_true"]:
            bad.append("guardrail")
        if bad:
            out.append(f"{r['id']}: {', '.join(bad)} | expected={r['expected']} got={r['got']}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="results directory")
    parser.add_argument("--min-routing", type=float, default=0.90)
    parser.add_argument("--live", action="store_true", help="do not force offline mode")
    args = parser.parse_args(argv)

    report = run(args.dataset, args.live)
    m = report["metrics"]

    print(f"{'metric':<22}{'value':>10}")
    print("-" * 32)
    for key, value in m.items():
        shown = f"{value:.1%}" if isinstance(value, float) and "ms" not in key else value
        print(f"{key:<22}{shown!s:>10}")

    fails = failures(report["results"])
    print(f"\nFailures ({len(fails)}):")
    for line in fails:
        print(f"  - {line}")

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_file = args.out / f"{stamp}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {out_file}")

    gate = m["intent_accuracy"] >= args.min_routing and m["errors"] == 0
    print(f"Routing gate (intent_accuracy >= {args.min_routing:.0%}, no crashes): "
          f"{'PASS' if gate else 'FAIL'}")
    return 0 if gate else 1


if __name__ == "__main__":
    raise SystemExit(main())

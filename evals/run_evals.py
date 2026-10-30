"""Offline evaluation runner for the multi-agent orchestration (NFR-07).

Runs every case of ``evals/dataset.jsonl`` (splits: official, heldout, guardrail) through
``Orchestrator().handle`` and reports, per split and overall: routing accuracy, plan accuracy, language
accuracy, tool precision/recall, handoff accuracy, guardrail accuracy, keyword groundedness and p50/p95
latency. The markdown table goes to stdout, the full report to ``evals/results/latest.json``.

Exit codes: 0 = gate passed, 1 = overall routing accuracy below ``--min-routing`` (CI gate), 2 = invalid dataset.

    python -m evals.run_evals                # deterministic: LLM_PROVIDER=offline, ENABLE_WEB_TOOLS=false
    python -m evals.run_evals --live         # use the provider / web tools configured in the environment
    python -m evals.run_evals --split heldout --min-routing 0.95
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals" / "dataset.jsonl"
DEFAULT_OUT = ROOT / "evals" / "results"
SPLITS = ("official", "heldout", "guardrail")
OFFLINE_ENV = {"LLM_PROVIDER": "offline", "ENABLE_WEB_TOOLS": "false", "GETNET_TODAY": "2026-10-30"}

REQUIRED_FIELDS = (
    "id", "split", "message", "user_id", "expected_intent", "expected_route", "expected_tools",
    "expect_handoff", "expect_blocked", "expected_language", "must_include_any",
)  # optional: must_not_include (strings that must NOT appear in the answer: PII, other customers' data)

METRICS = (
    ("routing_accuracy", "Routing accuracy (intent)"),
    ("plan_accuracy", "Plan accuracy (exact agent route)"),
    ("language_accuracy", "Language accuracy"),
    ("tool_precision", "Tool precision"),
    ("tool_recall", "Tool recall"),
    ("handoff_accuracy", "Handoff accuracy"),
    ("guardrail_accuracy", "Guardrail accuracy"),
    ("keyword_groundedness", "Keyword groundedness"),
)


# --------------------------------------------------------------------------- dataset


def load_dataset(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_no}: invalid JSON ({exc})") from exc
    return rows


def validate_dataset(rows: list[dict[str, Any]]) -> list[str]:
    """Return a list of problems (empty = valid)."""
    sys.path.insert(0, str(ROOT / "src"))
    from getnet_agents.schemas import AGENTS, INTENTS

    problems: list[str] = []
    seen: set[str] = set()
    for row in rows:
        rid = row.get("id", "<no id>")
        missing = [f for f in REQUIRED_FIELDS if f not in row]
        if missing:
            problems.append(f"{rid}: missing fields {missing}")
            continue
        if rid in seen:
            problems.append(f"{rid}: duplicated id")
        seen.add(rid)
        if row["split"] not in SPLITS:
            problems.append(f"{rid}: unknown split {row['split']!r}")
        if row["expected_intent"] not in INTENTS:
            problems.append(f"{rid}: unknown intent {row['expected_intent']!r}")
        if not row["expected_route"] or any(a not in AGENTS for a in row["expected_route"]):
            problems.append(f"{rid}: invalid route {row['expected_route']!r}")
        if row["expected_language"] not in ("pt", "en"):
            problems.append(f"{rid}: invalid language {row['expected_language']!r}")
        if not isinstance(row["expect_handoff"], bool) or not isinstance(row["expect_blocked"], bool):
            problems.append(f"{rid}: expect_handoff / expect_blocked must be booleans")
        for key in ("expected_tools", "must_include_any", "must_not_include"):
            if not isinstance(row.get(key, []), list):
                problems.append(f"{rid}: {key} must be a list")
    return problems


# --------------------------------------------------------------------------- scoring


def fold(text: str) -> str:
    """Lowercase and strip accents, so 'Clássica' matches 'classica'."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def score_case(case: dict[str, Any], resp: dict[str, Any] | None) -> dict[str, Any]:
    """Compare one response with its expectations. ``resp=None`` means the orchestrator crashed."""
    resp = resp or {}
    answer = fold(resp.get("answer", ""))
    called = {t["name"] for t in resp.get("tool_calls", [])}
    expected = set(case["expected_tools"])
    guardrail = resp.get("guardrail") or {}
    handoff = resp.get("handoff") or {}
    forbidden = [s for s in case.get("must_not_include", []) if fold(s) in answer]
    keywords = case["must_include_any"]
    return {
        "intent": resp.get("intent") == case["expected_intent"],
        "plan": resp.get("route") == case["expected_route"],
        "language": resp.get("language") == case["expected_language"],
        "tool_tp": len(called & expected),
        "tool_fp": len(called - expected),
        "tool_fn": len(expected - called),
        "handoff": bool(handoff.get("required")) == case["expect_handoff"],
        "guardrail": bool(guardrail.get("blocked")) == case["expect_blocked"] and not forbidden,
        "leaked": forbidden,
        "keywords": (any(fold(k) in answer for k in keywords) if keywords else None) if resp else (False if keywords else None),
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))]


def _ratio(num: int, den: int) -> float:
    return num / den if den else 1.0


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    checks = [r["checks"] for r in results]
    kw = [c["keywords"] for c in checks if c["keywords"] is not None]
    tp, fp, fn = (sum(c[k] for c in checks) for k in ("tool_tp", "tool_fp", "tool_fn"))
    lat = [r["latency_ms"] for r in results]
    return {
        "n": len(results),
        "crashes": sum(1 for r in results if r["error"]),
        "routing_accuracy": _ratio(sum(c["intent"] for c in checks), len(checks)),
        "plan_accuracy": _ratio(sum(c["plan"] for c in checks), len(checks)),
        "language_accuracy": _ratio(sum(c["language"] for c in checks), len(checks)),
        "tool_precision": _ratio(tp, tp + fp),
        "tool_recall": _ratio(tp, tp + fn),
        "handoff_accuracy": _ratio(sum(c["handoff"] for c in checks), len(checks)),
        "guardrail_accuracy": _ratio(sum(c["guardrail"] for c in checks), len(checks)),
        "keyword_groundedness": _ratio(sum(bool(k) for k in kw), len(kw)),
        "latency_p50_ms": round(statistics.median(lat), 2) if lat else 0.0,
        "latency_p95_ms": round(percentile(lat, 95), 2),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for split in SPLITS:
        subset = [r for r in results if r["split"] == split]
        if subset:
            groups[split] = aggregate(subset)
    groups["ALL"] = aggregate(results)
    return groups


def failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per case with at least one failed check, with expected vs. got."""
    out = []
    for r in results:
        c = r["checks"]
        failed = [k for k in ("intent", "plan", "language", "handoff", "guardrail") if not c[k]]
        if c["tool_fn"] or c["tool_fp"]:
            failed.append("tools")
        if c["keywords"] is False:
            failed.append("keywords")
        if r["error"] or failed:
            out.append({"id": r["id"], "split": r["split"], "message": r["message"], "failed": failed,
                        "error": r["error"], "expected": r["expected"], "got": r["got"], "leaked": c["leaked"]})
    return out


# --------------------------------------------------------------------------- running


def run_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from getnet_agents.orchestrator import Orchestrator
    from getnet_agents.schemas import ChatRequest

    orch = Orchestrator()
    results = []
    for case in rows:
        started = time.perf_counter()
        resp: dict[str, Any] | None
        try:
            resp = orch.handle(ChatRequest(message=case["message"], user_id=case["user_id"])).model_dump()
            error = None
        except Exception as exc:  # noqa: BLE001 - an eval reports crashes as failures instead of stopping
            resp, error = None, f"{type(exc).__name__}: {exc}"
        latency = (time.perf_counter() - started) * 1000
        results.append({
            "id": case["id"], "split": case["split"], "message": case["message"],
            "expected": {k: case[k] for k in ("expected_intent", "expected_route", "expected_tools",
                                              "expect_handoff", "expect_blocked", "expected_language")},
            "got": {
                "intent": (resp or {}).get("intent"), "route": (resp or {}).get("route"),
                "language": (resp or {}).get("language"),
                "tools": [t["name"] for t in (resp or {}).get("tool_calls", [])],
                "sources": len((resp or {}).get("sources", [])),
                "handoff": bool(((resp or {}).get("handoff") or {}).get("required")),
                "blocked": bool(((resp or {}).get("guardrail") or {}).get("blocked")),
                "answer": ((resp or {}).get("answer") or "")[:240],
            },
            "checks": score_case(case, resp), "error": error, "latency_ms": round(latency, 2),
        })
    return results


# --------------------------------------------------------------------------- reporting


def _fmt(key: str, value: Any) -> str:
    if key == "n" or key == "crashes":
        return str(value)
    if key.endswith("_ms"):
        return f"{value:.1f}"
    return f"{value:.1%}"


def render_markdown(groups: dict[str, dict[str, Any]], min_routing: float) -> str:
    cols = list(groups)
    lines = ["| Metric | " + " | ".join(cols) + " |", "|---|" + "---:|" * len(cols)]
    rows = [("n", "Cases")] + list(METRICS) + [("latency_p50_ms", "Latency p50 (ms)"),
                                               ("latency_p95_ms", "Latency p95 (ms)"), ("crashes", "Crashes")]
    for key, label in rows:
        bold = "**" if key == "routing_accuracy" else ""
        lines.append(f"| {bold}{label}{bold} | " + " | ".join(f"{bold}{_fmt(key, groups[c][key])}{bold}" for c in cols) + " |")
    gate = groups["ALL"]["routing_accuracy"] >= min_routing
    lines.append(f"\nGate: overall routing accuracy {groups['ALL']['routing_accuracy']:.1%} "
                 f"(minimum {min_routing:.0%}) → **{'PASS' if gate else 'FAIL'}**")
    return "\n".join(lines)


def render_failures(fails: list[dict[str, Any]]) -> str:
    if not fails:
        return "\nNo failing cases."
    lines = [f"\n### Failing cases ({len(fails)})"]
    for f in fails:
        exp, got = f["expected"], f["got"]
        lines.append(f"- `{f['id']}` ({f['split']}) failed: {', '.join(f['failed']) or 'crash'} — \"{f['message'][:80]}\"")
        if f["error"]:
            lines.append(f"  - CRASH {f['error']}")
        else:
            lines.append(f"  - expected intent={exp['expected_intent']} route={exp['expected_route']} "
                         f"tools={exp['expected_tools']} handoff={exp['expect_handoff']} blocked={exp['expect_blocked']}")
            lines.append(f"  - got      intent={got['intent']} route={got['route']} tools={got['tools']} "
                         f"handoff={got['handoff']} blocked={got['blocked']}" + (f" LEAKED={f['leaked']}" if f["leaked"] else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="results directory (writes latest.json)")
    ap.add_argument("--min-routing", type=float, default=0.90, help="gate on overall routing accuracy")
    ap.add_argument("--split", choices=SPLITS, help="run a single split")
    ap.add_argument("--live", action="store_true", help="do not force offline mode")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not args.live:
        os.environ.update(OFFLINE_ENV)  # before importing the package, so config reads the offline values
    os.environ.setdefault("LOG_LEVEL", "WARNING")  # keep per-span JSON logs out of the report on stdout
    sys.path.insert(0, str(ROOT / "src"))

    rows = load_dataset(args.dataset)
    problems = validate_dataset(rows)
    if problems:
        print("Invalid dataset:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        return 2
    if args.split:
        rows = [r for r in rows if r["split"] == args.split]

    results = run_cases(rows)
    groups = summarize(results)
    fails = failures(results)

    print(render_markdown(groups, args.min_routing))
    print(render_failures(fails))

    args.out.mkdir(parents=True, exist_ok=True)
    report = {"mode": "live" if args.live else "offline", "min_routing": args.min_routing,
              "metrics": groups, "failures": fails, "results": results}
    (args.out / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {args.out / 'latest.json'}")
    return 0 if groups["ALL"]["routing_accuracy"] >= args.min_routing else 1


if __name__ == "__main__":
    raise SystemExit(main())

# Evaluation

Golden set + deterministic runner for the multi-agent orchestration.

## Run

```bash
python evals/run_evals.py                 # offline: LLM_PROVIDER=offline, ENABLE_WEB_TOOLS=false, GETNET_TODAY=2026-10-30
python evals/run_evals.py --live          # uses the real provider/tools configured in the environment
python evals/run_evals.py --min-routing 0.95 --dataset evals/dataset.jsonl --out evals/results
```

The runner prints a metric table, the list of failing cases (expected vs. got) and writes a full report to `evals/results/<UTC timestamp>.json` (git-ignored). Exit code is `1` when intent accuracy is below `--min-routing` (default 0.90) or any case crashes — that is the CI gate.

## Dataset (`dataset.jsonl`)

One case per line: `id, message, user_id, expected_intent, expected_route, expected_language, must_have_sources, expected_tools, expected_handoff (null = don't care), expected_blocked`. It holds the 10 scenarios from the case (`case-*`), their Portuguese versions (`pt-*`), paraphrases (`para-*`) and adversarial/edge cases (`extra-*`: human request, prompt injection, out of scope, PII).

## Metrics

| Metric | What it measures |
|---|---|
| `intent_accuracy` | Router classified the intent correctly (**gate**) |
| `route_exact_match` | The exact agent sequence matches (`["router","support","knowledge"]` …) |
| `language_accuracy` | Answer language equals the question language |
| `source_coverage` | Cases that must be grounded returned ≥1 source, all with `https://` URLs |
| `tool_recall` | Expected tools that were actually called |
| `handoff_accuracy` | Human handoff opened exactly when expected |
| `guardrail_precision` / `guardrail_recall` | Blocked the unsafe/out-of-scope requests, and only those |
| `latency_p50_ms` / `latency_p95_ms` | End-to-end latency per request |

## In production

- **Golden set grows from traffic**: sampled, PII-masked conversations labelled by support specialists; every incident becomes a regression case.
- **LLM-as-judge** (`--live`) with a fixed rubric per answer: faithfulness to the cited sources (0–2), answers the actual question (0–2), correct language and tone (0–1), safe/no invented data (pass/fail). The judge uses a different model from the generator and is itself calibrated against a human-labelled subset.
- **Regression in CI**: the offline run blocks merges (routing gate); the live run is nightly and alerts on drops vs. the last baseline.
- **Online signals**: handoff rate, guardrail block rate, tool error rate and p95 latency from `/metrics`, with alerts on sustained deviation.

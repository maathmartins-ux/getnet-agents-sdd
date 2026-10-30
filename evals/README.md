# Evaluation

Golden set + deterministic runner for the multi-agent orchestration (spec NFR-07). One command, no API key, no network.

```bash
python -m evals.run_evals                      # offline: LLM_PROVIDER=offline, ENABLE_WEB_TOOLS=false, GETNET_TODAY=2026-10-30
python -m evals.run_evals --split heldout      # a single split
python -m evals.run_evals --live               # uses the provider / web tools configured in the environment
python -m evals.run_evals --min-routing 0.95
```

Prints a markdown table (per split and overall) plus the list of failing cases (expected vs. got), and writes the full
report to `evals/results/latest.json` (git-ignored). **Exit code `1` when the overall routing accuracy is below
`--min-routing` (default 90 %) — this is the CI gate**; `2` when the dataset is invalid.

## Dataset — `dataset.jsonl`

| Split | Cases | Purpose |
|---|---|---|
| `official` | 10 | The 10 scenarios of the case, verbatim (spec AC-01..AC-10). Same cases as `tests/test_scenarios.py`. |
| `heldout` | 22 | Paraphrases in PT and EN, plus multi-intent messages (e.g. *"Maquininha sem internet e quero falar com humano"*). Never used to tune the router rules, so it measures generalisation. |
| `guardrail` | 6 | Prompt injection, another customer's data, PII in the message, offensive content, off-topic, fraud. |

Fields: `id, split, message, user_id, expected_intent, expected_route, expected_tools, expect_handoff, expect_blocked,
expected_language, must_include_any` and the optional `must_not_include` (strings that must **not** appear in the
answer: PII, other customers' data). Expectations are derived from the spec (BR-03, BR-05, FR-08), not from what the
system currently does — a failing case is a finding, not a dataset bug.

## Metrics

| Metric | Definition |
|---|---|
| Routing accuracy | `intent == expected_intent` (**gate**, overall) |
| Plan accuracy | `route == expected_route`, exact agent sequence |
| Language accuracy | answer language equals the question language (BR-02) |
| Tool precision / recall | micro-averaged over cases: tools called vs. `expected_tools` (extra calls hurt precision, missing calls hurt recall) |
| Handoff accuracy | `handoff.required == expect_handoff` |
| Guardrail accuracy | `guardrail.blocked == expect_blocked` **and** no `must_not_include` string leaked into the answer |
| Keyword groundedness | answer contains ≥ 1 of `must_include_any` (accent/case-insensitive); only cases that declare keywords. Cheap lexical proxy for "the answer is about the right thing"; web-tool cases declare none because tools are disabled offline |
| Latency p50 / p95 | wall-clock per request, in-process (excludes HTTP) |

A crashing case counts as a miss in every metric (and in `Crashes`), never as a skipped case.

## In production

- **Golden set grows from traffic**: PII-masked conversations sampled and labelled by support specialists; every incident becomes a regression case in `heldout`.
- **LLM-as-judge** (`--live`, nightly) with a fixed rubric: faithfulness to the cited sources (0–2), answers the actual question (0–2), language and tone (0–1), no invented data (pass/fail). The judge is a different model from the generator and is calibrated against a human-labelled subset.
- **Regression in CI**: the offline run blocks merges (routing gate); the live run compares with the last baseline and alerts on drops.
- **Online signals** from `/metrics`: handoff rate, guardrail block rate, tool error rate and p95 latency, with alerts on sustained deviation.

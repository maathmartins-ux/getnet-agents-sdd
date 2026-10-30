# Data model 001

## API (see `schemas.py`)
`ChatRequest{message, user_id}` → `ChatResponse{trace_id, answer, language, route, intent, confidence, sources[], tool_calls[], handoff?, guardrail?, mode, latency_ms}`.

## Internal messages
- `RoutingDecision{intent, language, plan: [agent], confidence, rationale}` — Router → Orchestrator.
- `AgentResult{agent, answer, sources[], tool_calls[], grounded, needs_handoff, handoff_reason, facts{}}` — agent → Orchestrator → next agent.

## Knowledge corpus — `data/knowledge/corpus.jsonl` (one JSON per line)
| field | type | note |
|-------|------|------|
| id | str | unique |
| title | str | page/section title |
| url | str | real source URL (cited in answers) |
| lang | "pt"\|"en" | same topic exists in both |
| section | str | topic group |
| tags | list[str] | pt + en keywords |
| text | str | 60–250 words |

`data/knowledge/sources.json`: `[{url, title, lang}]` — URLs ingested by `rag/ingest.py --refresh`.

## Index entry (in memory, `rag/store.py`)
`Doc{id, title, url, lang, tags, text}` + per-field term frequencies and lengths; `Hit{doc, score (raw BM25), norm (0–1)}`.

## CRM mock — `data/crm/customers.json`
`{customers: {user_id: {name, document(masked), plan, receivables_plan, terminals[{serial, model, status, last_error}], transactions[{id, date, amount, product(debit|credit|pix), installments, status(approved|declined), decline_code?, decline_reason?}]}}}`; `holidays.json`: list of ISO dates.

## Tool result
`ToolResult{ok, data, source, error}` — `source` is a URL or `crm://<table>`.

## Ticket
`{ticket_id: "GN-xxxxxxxx", user_id, category, summary, status: "open"}` (deterministic id).

## Evaluation case — `evals/dataset.jsonl`
`{id, message, user_id, expected_intent, expected_route, expected_language, must_have_sources, expected_tools, expected_handoff, expected_blocked}`.

# Spec 001 — Multi-Agent Support System for Getnet merchants

Status: approved · Owner: Matheus G. Martins · Date: 2026-10-30 · Constitution: `.specify/memory/constitution.md`

## 1. Problem
Getnet merchants ask three kinds of question in the same chat: about Getnet products, about their
own account (settlements, transactions, terminals) and general questions (weather, FX). One
monolithic prompt mixes these concerns, invents facts and cannot be measured. We need a small team of
specialised agents behind a single HTTP endpoint, grounded in Getnet's public website and in the
merchant's data, that knows when to hand off to a human.

## 2. User stories
- **US1 — Product question.** As a merchant, I ask about a Getnet product and get an answer grounded in getnet.net, with the source links.
- **US2 — My account.** As a merchant, I ask when my money arrives / why a sale failed / why my terminal is offline, and the system looks up *my* data with tools.
- **US3 — General question.** As a merchant, I ask a general question (weather, exchange rate) and get a live answer from a web tool, or an honest "could not retrieve".
- **US4 — Human.** As a merchant, I ask for a person, or the system is not confident, and I get a ticket and a human handoff.
- **US5 — Safety.** As Getnet, abusive, injected or off-topic messages are refused and personal data never leaks into logs or answers.
- **US6 — Operator.** As an engineer, I build/run/test with one command and can see traces, metrics and an evaluation score.

## 3. Functional requirements
| ID | Requirement |
|----|-------------|
| FR-01 | `POST /chat` accepts `ChatRequest` and returns `ChatResponse` (§5). `GET /health`, `GET /metrics`. |
| FR-02 | **Router agent** is the entry point: classifies intent (7 labels), detects language (pt/en), produces an ordered agent plan and a confidence score. |
| FR-03 | **Knowledge agent** answers product questions with RAG over an ingested snapshot of Getnet web pages (ingestion → storage → retrieval → generation) and cites `sources` (title, url, score). |
| FR-04 | **Knowledge agent** answers general questions with web tools: weather, exchange rate, web search. |
| FR-05 | **Support agent** resolves account questions with ≥2 CRM tools (profile, settlement forecast, transactions, terminal status, open ticket) via an LLM tool-use loop. |
| FR-06 | **Escalation agent** (4th agent) opens a support ticket and returns a human handoff (`handoff.required=true`, `ticket_id`). |
| FR-07 | Orchestrator executes the plan, passing each agent's `AgentResult` (facts, sources) to the next one; technical issues run Support then Knowledge. |
| FR-08 | Input guardrails: prompt injection → blocked (`unsafe`); off-topic → blocked (`out_of_scope`); PII (CPF, CNPJ, card PAN, e-mail, phone) masked → `guardrail.category="pii_redacted"`, not blocked. |
| FR-09 | Output guardrail: product answers must be grounded in retrieved text (lexical support score ≥ threshold); otherwise fallback to extractive answer or escalation. |
| FR-10 | Every response carries `trace_id`, `route`, `tool_calls` (name, args, ok, latency_ms), `mode`, `latency_ms`. |
| FR-11 | Two LLM providers behind one interface: `offline` (deterministic) and `anthropic` (Claude Haiku for routing, Claude Sonnet for agents). |

## 4. Non-functional requirements
| ID | Requirement |
|----|-------------|
| NFR-01 | Runs and passes all tests with no API key and no network (`LLM_PROVIDER=offline`, `ENABLE_WEB_TOOLS=false`). |
| NFR-02 | LLM calls: timeout, bounded retries with backoff, automatic fallback to offline on failure. |
| NFR-03 | Tools: 5 s timeout, never raise. |
| NFR-04 | Observability: structured JSON logs (one line per span, with `trace_id`), Prometheus counters/histograms per agent, intent, tool and outcome. |
| NFR-05 | Container: `python:3.12-slim`, non-root user, `HEALTHCHECK`, one-command start with docker compose. |
| NFR-06 | Offline p95 latency < 200 ms per request (excluding live web tools). |
| NFR-07 | Evaluation dataset with CI gate: intent routing accuracy ≥ 90 %. |
| NFR-08 | Typed, modular code; prompts as versioned `.md` files. |

## 5. Contracts (mirrored exactly in `src/getnet_agents/schemas.py`)
- `ChatRequest`: `message` (1–2000 chars), `user_id` (str).
- `ChatResponse`: `trace_id`, `answer`, `language` ("pt"|"en"), `route` (list[str], e.g. `["router","support","knowledge"]`), `intent`, `confidence` (0–1), `sources` (list[{title, url, score}]), `tool_calls` (list[{name, args, ok, latency_ms}]), `handoff` ({required: bool, reason, ticket_id} | null), `guardrail` ({blocked: bool, category, reason} | null), `mode` ("offline"|"anthropic"), `latency_ms`.
- `Orchestrator().handle(req: ChatRequest) -> ChatResponse`.
- Agent labels: `router`, `knowledge`, `support`, `escalation`. Intents: `product_info`, `general_web`, `account_support`, `technical_support`, `human_request`, `out_of_scope`, `unsafe`.
- Tools: `ToolResult = {ok: bool, data: dict | list, source: str, error: str | None}`; never raise; 5 s timeout; honour `ENABLE_WEB_TOOLS=false` (`ok=False, error="disabled"`).
  - `crm.py`: `get_customer_profile(user_id)`, `get_settlement_forecast(user_id, reference_date: str | None)`, `get_recent_transactions(user_id, status: str | None, limit: int = 10)`, `get_terminal_status(user_id)`, `open_support_ticket(user_id, category, summary)`.
  - `web.py`: `get_weather_forecast(city, days_ahead=1)` [Open-Meteo], `get_exchange_rate(base="EUR", quote="BRL")` [AwesomeAPI], `web_search(query, k=5)` [DuckDuckGo HTML].
  - `tools/__init__.py`: `TOOL_SPECS` (Anthropic tool-use JSON Schema) and `HANDLERS {name: fn}`.

## 6. Business rules
| ID | Rule |
|----|------|
| BR-01 | No source, no claim: a Getnet product answer without ≥1 retrieved source above the relevance threshold is escalated, never generated. |
| BR-02 | Answer language = question language. |
| BR-03 | A user only sees data for their own `user_id`; unknown user → honest message + handoff offer. |
| BR-04 | Router confidence < 0.45 → Escalation appended to the plan. |
| BR-05 | Explicit request for a human always reaches Escalation, which opens a ticket. |
| BR-06 | Settlement dates are business days (weekends and national holidays skipped). |
| BR-07 | PII is masked before any agent, LLM call or log line sees the message. |
| BR-08 | Web-tool failure on a general question → honest "could not retrieve now" (no handoff; it is not a Getnet issue). |

## 7. Acceptance criteria — the 10 scenarios of the case (user_id `cliente1988`)
Offline mode, `ENABLE_WEB_TOOLS=false`, `GETNET_TODAY=2026-10-30`.

| # | Message | intent | route | evidence |
|---|---------|--------|-------|----------|
| AC-01 | What's the difference between the Get Clássica and the Get Smart? | product_info | router→knowledge | sources ≥1 |
| AC-02 | What's the weather forecast in Porto Alegre tomorrow? | general_web | router→knowledge | tool `get_weather_forecast` |
| AC-03 | When will the money from yesterday's sales be deposited? | account_support | router→support | tool `get_settlement_forecast` |
| AC-04 | Do I need a bank account to receive my sales via Pix? | product_info | router→knowledge | sources ≥1 |
| AC-05 | My card machine won't connect to the internet, what should I do? | technical_support | router→support→knowledge | tool `get_terminal_status`, sources ≥1 |
| AC-06 | How does receivables advance (antecipação) work with Getnet? | product_info | router→knowledge | sources ≥1 |
| AC-07 | What's the euro exchange rate today? | general_web | router→knowledge | tool `get_exchange_rate` |
| AC-08 | My card machine is showing a transaction decline error. | technical_support | router→support→knowledge | tool `get_recent_transactions` |
| AC-09 | How many installments can I split a sale into with the crediário? | product_info | router→knowledge | sources ≥1 or handoff |
| AC-10 | Can I sell through WhatsApp using the Payment Link? | product_info | router→knowledge | sources ≥1 |

Extra criteria: AC-11 "quero falar com um atendente" → human_request, router→escalation, ticket_id.
AC-12 prompt injection → unsafe, blocked. AC-13 "write me a poem about cats" → out_of_scope, blocked.
AC-14 message with a CPF → `pii_redacted`, CPF absent from answer and logs. All answers in the question language.

## 8. Out of scope
Authentication (user_id is trusted, as in the case), persistent conversation memory, real CRM integration
(mock data in `data/crm/`), payments execution.

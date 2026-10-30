# Constitution — Getnet Multi-Agent Support

Ratified 2026-10-30. Every spec, plan, task and line of code is checked against these articles.
A change that violates an article requires amending this file first, in its own commit.

## Article I — Spec first
No code without a requirement. Every module traces to an ID in `specs/001-multi-agent-support/spec.md`
(FR/NFR/BR), and every requirement traces to at least one automated test (`tasks.md`).
Contracts live in `src/getnet_agents/schemas.py` and change only together with the spec.

## Article II — Reliability over cleverness
The system must answer correctly without an LLM (`LLM_PROVIDER=offline`, deterministic, no network)
and degrade gracefully when the LLM or a tool fails: timeouts, bounded retries, fallback to the
offline path. Tools never raise; they return `ToolResult(ok=False, error=...)`.
**No invented answers:** a Getnet claim without a retrieved source is never emitted — the request
goes to the Escalation agent instead.

## Article III — Observability
Every request has a `trace_id`, one structured JSON log line per agent span, and Prometheus
metrics (`/metrics`). If it is not measured, it is not done. Quality is measured by an offline
evaluation set with a CI gate (routing accuracy ≥ 90 %).

## Article IV — Safety and guardrails
Input is screened for prompt injection, out-of-scope requests and PII (CPF, card numbers are
masked before reaching any agent, log or LLM). Output is checked for grounding. When the system
cannot resolve a request with confidence, it hands off to a human with a ticket — never guesses.

## Article V — Simplicity
Standard library and a few well-known packages (FastAPI, httpx, pydantic, anthropic). No heavy
framework where 100 lines of readable code suffice (BM25 instead of a vector DB at this corpus
size). One command builds and runs it (`docker compose up --build`).

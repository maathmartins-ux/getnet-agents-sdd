# Tasks 001

Each task links requirement → module → test. `[P]` = parallel (separate owner / files).

| # | Task | Req | Module | Test |
|---|------|-----|--------|------|
| T01 | Contracts | FR-01, FR-10 | `schemas.py` | `test_api.py` |
| T02 | Config from env | NFR-01, FR-11 | `config.py` | `test_orchestrator.py` |
| T03 [P] Fork A | Getnet knowledge snapshot pt/en with real URLs | FR-03, BR-01 | `data/knowledge/` | `test_rag.py` |
| T04 [P] Fork B | CRM + web tools, TOOL_SPECS, HANDLERS, mock CRM | FR-04, FR-05, NFR-03, BR-06 | `tools/`, `data/crm/` | `test_tools.py` |
| T05 [P] Fork C | Eval dataset + runner (gate ≥ 90 %), scenario tests, CI, video script | NFR-07, AC-01..14 | `evals/`, `.github/` | `test_scenarios.py` |
| T06 | LLM layer: Anthropic (timeout/retry/fallback) + Offline | FR-11, NFR-02 | `llm.py` | `test_orchestrator.py` |
| T07 | Ingestion + BM25 store with field weights | FR-03 | `rag/` | `test_rag.py` |
| T08 | Router agent | FR-02, BR-02, BR-04 | `agents/router.py`, `prompts/router.md` | `test_router.py` |
| T09 | Knowledge agent (RAG + web) | FR-03, FR-04, BR-01, BR-08 | `agents/knowledge.py` | `test_orchestrator.py`, `test_scenarios.py` |
| T10 | Support agent (tool-use loop) | FR-05, BR-03 | `agents/support.py` | `test_orchestrator.py` |
| T11 | Escalation agent | FR-06, BR-05 | `agents/escalation.py` | `test_orchestrator.py` |
| T12 | Guardrails in/out | FR-08, FR-09, BR-07 | `guardrails.py` | `test_guardrails.py` |
| T13 | Orchestrator | FR-07, FR-10 | `orchestrator.py` | `test_orchestrator.py` |
| T14 | Observability: logs, spans, metrics | NFR-04 | `observability.py` | `test_observability.py` |
| T15 | API | FR-01 | `api.py` | `test_api.py` |
| T16 | Docker + compose + Makefile | NFR-05 | `Dockerfile`, `docker-compose.yml` | curl of AC-01..10 |
| T17 | README with traceability matrix + SDD timeline | NFR-08 | `README.md` | review |

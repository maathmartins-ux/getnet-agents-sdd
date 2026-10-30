"""HTTP API (FR-01): POST /chat, GET /health, GET /metrics."""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import __version__
from .config import get_settings
from .observability import current_trace, log_event, render_metrics
from .orchestrator import Orchestrator
from .rag.store import load_store
from .schemas import ChatRequest, ChatResponse

_state: dict[str, Orchestrator] = {}


def orchestrator() -> Orchestrator:
    if "orc" not in _state:
        _state["orc"] = Orchestrator()
    return _state["orc"]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    orchestrator()
    load_store(str(get_settings().knowledge_path))  # warm the index before the first request
    yield


app = FastAPI(
    title="Getnet Multi-Agent Support",
    version=__version__,
    description="Router, Knowledge (RAG + web), Support (CRM tools) and Escalation agents behind one endpoint.",
    lifespan=lifespan,
)


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    return orchestrator().handle(req)


@app.get("/health")
def health() -> dict[str, object]:
    s = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "mode": orchestrator().llm.mode,
        "web_tools": s.enable_web_tools,
        "documents": len(load_store(str(s.knowledge_path))),
    }


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    return render_metrics()


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    log_event("unhandled_error", error=type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "internal_error", "trace_id": current_trace()})

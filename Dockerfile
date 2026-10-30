FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KNOWLEDGE_PATH=/app/data/knowledge/corpus.jsonl \
    CRM_DATA_PATH=/app/data/crm/customers.json \
    CRM_HOLIDAYS_PATH=/app/data/crm/holidays.json

WORKDIR /app

# Dependencies first: this layer is cached while only the code changes.
COPY pyproject.toml ./
COPY src ./src
# Editable install: the package runs from /app/src, so data/ paths resolve to /app/data.
RUN pip install -e ".[dev]"

COPY data ./data
COPY tests ./tests
COPY evals ./evals

RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "getnet_agents.api:app", "--host", "0.0.0.0", "--port", "8000"]

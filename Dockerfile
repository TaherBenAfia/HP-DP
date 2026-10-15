FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PREFECT_TELEMETRY_ENABLED=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY platform_cli ./platform_cli
COPY configs ./configs
COPY scripts ./scripts
COPY tests ./tests
COPY sharing ./sharing
COPY app.py ./

RUN pip install -e ".[platform]"

EXPOSE 8080 8501

# Default: read-only API. docker-compose overrides for the dashboard/prefect.
CMD ["uvicorn", "src.sharing.api:app", "--host", "0.0.0.0", "--port", "8080"]

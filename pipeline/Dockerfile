FROM python:3.12-slim AS base

WORKDIR /workspace/pipeline

COPY .gitignore /workspace/.gitignore
COPY .env.example /workspace/.env.example
COPY compose.yaml /workspace/compose.yaml
COPY .github/workflows /workspace/.github/workflows
COPY config /workspace/config
COPY pipeline/pyproject.toml ./
COPY pipeline/Dockerfile ./Dockerfile
COPY site/.dockerignore /workspace/site/.dockerignore
COPY pipeline/recsys_daily ./recsys_daily
COPY pipeline/tests ./tests

RUN pip install --no-cache-dir ".[dev]"

ENTRYPOINT ["python", "-m", "recsys_daily"]

FROM base AS similarity

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir ".[similarity]"

FROM base AS pipeline

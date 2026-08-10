FROM python:3.12-slim

WORKDIR /workspace/pipeline

COPY .gitignore /workspace/.gitignore
COPY .env.example /workspace/.env.example
COPY config /workspace/config
COPY pipeline/pyproject.toml ./
COPY pipeline/Dockerfile ./Dockerfile
COPY pipeline/recsys_daily ./recsys_daily
COPY pipeline/tests ./tests

RUN pip install --no-cache-dir ".[dev]"

ENTRYPOINT ["python", "-m", "recsys_daily"]

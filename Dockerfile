FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir -e ".[dev]"

RUN mkdir -p /data/uploads /workspace

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

CMD ["telegram-cursor-agent"]

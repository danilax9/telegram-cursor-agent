FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    xz-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

ARG NODE_VERSION=22.14.0
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
    | tar -xJ -C /usr/local --strip-components=1 \
    && node -v && npx -v

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

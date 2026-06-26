FROM node:22-alpine AS frontend
WORKDIR /app/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml* ./
COPY web/pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.13-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_SYSTEM_PYTHON=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        clang \
        cmake \
        ffmpeg \
        git \
        ninja-build \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=frontend /app/web/build ./src/stt_vault/static

RUN uv pip install --system .

EXPOSE 8080
CMD ["python", "-m", "stt_vault.app"]

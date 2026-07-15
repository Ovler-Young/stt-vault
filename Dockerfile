FROM node:22-alpine AS frontend
WORKDIR /app/web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml* ./
COPY web/pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_SYSTEM_PYTHON=1
ENV XDG_CACHE_HOME=/data/cache
ENV HOME=/data
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
COPY pyproject.toml uv.lock README.md ./
RUN uv export --locked --no-dev --no-emit-project -o requirements.txt \
    && uv pip install --system -r requirements.txt

COPY src ./src
COPY --from=frontend /app/web/build ./src/stt_vault/static

RUN uv pip install --system --no-deps .

EXPOSE 8080
CMD ["python", "-m", "stt_vault.app"]

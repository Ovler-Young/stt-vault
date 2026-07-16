# syntax=docker/dockerfile:1.7

FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS frontend
WORKDIR /app/web
ENV PNPM_HOME=/pnpm
ENV PATH=${PNPM_HOME}:${PATH}
RUN corepack enable && pnpm config set store-dir /pnpm/store
COPY web/package.json web/pnpm-lock.yaml* ./
COPY web/pnpm-workspace.yaml* ./
RUN --mount=type=cache,target=/pnpm/store pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28 AS backend-base
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_SYSTEM_PYTHON=1
ENV UV_CACHE_DIR=/var/cache/uv
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

COPY --from=ghcr.io/astral-sh/uv@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /usr/local/bin/uv

FROM backend-base AS backend-dependencies-cpu
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/var/cache/uv \
    uv export --locked --no-dev --extra cpu --no-emit-project -o requirements.txt \
    && uv pip install --system -r requirements.txt

FROM backend-base AS backend-dependencies-gpu
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/var/cache/uv \
    uv export --locked --no-dev --extra gpu --no-emit-project -o requirements.txt \
    && uv pip install --system -r requirements.txt

FROM backend-dependencies-cpu AS cpu
COPY README.md ./
COPY src ./src
COPY --from=frontend /app/web/build ./src/stt_vault/static

RUN --mount=type=cache,target=/var/cache/uv uv pip install --system --no-deps .

EXPOSE 8080
CMD ["python", "-m", "stt_vault.app"]

FROM backend-dependencies-gpu AS gpu
COPY README.md ./
COPY src ./src
COPY --from=frontend /app/web/build ./src/stt_vault/static

RUN --mount=type=cache,target=/var/cache/uv uv pip install --system --no-deps .

EXPOSE 8080
CMD ["python", "-m", "stt_vault.app"]

FROM cpu AS default

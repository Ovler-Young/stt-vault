# syntax=docker/dockerfile:1.7

FROM node:22-alpine@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS frontend
WORKDIR /app/web
ENV PNPM_HOME=/pnpm
ENV PATH=${PNPM_HOME}:${PATH}
RUN corepack enable && pnpm config set store-dir /pnpm/store
COPY web/package.json web/pnpm-lock.yaml* ./
COPY web/pnpm-workspace.yaml* ./
RUN --mount=type=cache,target=/pnpm/store pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS backend-base
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
    && uv pip install --system \
        --index-url https://pypi.org/simple \
        --find-links https://download.pytorch.org/whl/cpu/torch/ \
        --find-links https://download.pytorch.org/whl/cpu/torchaudio/ \
        -r requirements.txt

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
CMD ["python", "-m", "stt_vault.core.app"]

FROM backend-dependencies-gpu AS gpu
COPY README.md ./
COPY src ./src
COPY --from=frontend /app/web/build ./src/stt_vault/static

RUN --mount=type=cache,target=/var/cache/uv uv pip install --system --no-deps .

EXPOSE 8080
CMD ["python", "-m", "stt_vault.core.app"]

FROM cpu AS default

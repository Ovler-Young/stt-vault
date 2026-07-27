# STT Vault

Private speech-to-text vault for speaker-aware transcripts.

STT Vault combines:

- Senko speaker diarization
- OpenAI audio transcription
- Persistent speaker identity matching by centroid similarity
- A single-port web UI and API
- Export formats for JSON, Whisper-like JSON, AI-readable text, SRT, VTT, RTTM, and Hyperaudio-style HTML

## Quick Start

Use the published CPU image for normal deployment:

```sh
mkdir -p ./data
docker compose pull
docker compose up -d
```

Open `http://localhost:8080`.

The `latest` and `cpu` tags are CPU-only. They do not include NVIDIA CUDA libraries.

For an NVIDIA host with the NVIDIA Container Toolkit installed, run the separate GPU image:

```sh
docker compose -f docker-compose.yml -f docker-compose.gpu.yml pull
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

The GPU override uses the `gpu` image tag, exposes all host GPUs, and sets `SENKO_DEVICE=cuda`.

For a private GitHub package, log in to GHCR before pulling:

```sh
gh auth token | docker login ghcr.io -u USERNAME --password-stdin
```

The compose file mounts an inline Docker Compose `config` as `/app/.env`; no host `.env` or separate config file is required. Edit the `stt_vault_environment` config content in `docker-compose.yml` for deployment. `OPENAI_API_KEY` is required for transcription. Set `STT_HOST_DATA_DIR` to choose the host data directory and `APP_PORT` to choose the published port:

```sh
STT_HOST_DATA_DIR=/srv/stt-vault APP_PORT=8080 docker compose up -d
```

For local image builds, use the build override:

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

Build and start the GPU target locally with:

```sh
STT_BUILD_TARGET=gpu docker compose \
  -f docker-compose.yml \
  -f docker-compose.gpu.yml \
  -f docker-compose.build.yml up --build
```

## Configuration

Required:

- `STT_DATA_DIR`: persistent media and export directory
- `STT_DB_PATH`: SQLite database path
- `OPENAI_API_KEY`: OpenAI-compatible API key
- `OPENAI_BASE_URL`: OpenAI-compatible API base URL
- `OPENAI_TRANSCRIBE_MODEL`: transcription model, for example `gpt-4o-transcribe`

Important optional settings:

- `OPENAI_TRANSCRIBE_PROMPT`: prompt sent to supported transcription models
- `OPENAI_CONCURRENCY`: concurrent transcription requests
- `DIARIZATION_CONCURRENCY`: concurrent local diarization jobs, usually `1` on CPU
- `DIARIZER_IDLE_TIMEOUT_SECONDS`: unload the in-process Senko diarizer after idle time
- `SENKO_BATCHED_EMBEDDINGS`: process Senko fbank and embeddings in batches before global clustering
- `SENKO_FBANK_BATCH_SEGMENTS`: number of Senko subsegments per fbank/embedding batch
- `SPEAKER_SIMILARITY_THRESHOLD`: centroid similarity threshold for speaker identity matching
- `ADMIN_PASSWORD`: password accepted only by `POST /api/auth/token`
- `JWT_SECRET`: required signing secret for application-issued JWT access tokens
- `JWT_ISSUER`, `JWT_AUDIENCE`: JWT validation claims, with defaults suitable for this application
- `JWT_ACCESS_TOKEN_MINUTES`: access token lifetime in minutes. The default `0` issues a
  non-expiring token; use a positive value to issue tokens with a finite lifetime.

All protected API endpoints require `Authorization: Bearer <access-token>`. Obtain an
access token through `POST /api/auth/token` with the configured administrator password.

## Runtime Checks

```sh
docker compose ps
curl -fsS http://localhost:${APP_PORT:-8080}/api/health
```

Inspect operational failures through the structured container logs and the persisted event history:

```sh
docker compose logs --tail=200 stt-vault
docker compose exec stt-vault python -c 'import os, sqlite3; connection = sqlite3.connect(os.environ["STT_DB_PATH"]); print(*connection.execute("SELECT level, stage, message, created_at FROM job_events ORDER BY id DESC LIMIT 50"), sep="\\n")'
```

Log records include stable `event_name` values such as `media.stream_failed`, `asset_id`, `job_id`,
process return codes, and bounded diagnostics. Credentials and filesystem paths are redacted. The API
and database retain only categorized user-facing failures.

## Development

```sh
uv venv --python 3.13 .venv
uv sync --extra cpu --extra dev
cd web
pnpm install
pnpm build
cd ..
uvicorn stt_vault.core.app:create_app --factory --reload
```

The frontend build is copied into `src/stt_vault/static` by Docker. During local development, run `pnpm build` in `web/` and copy `web/build` to `src/stt_vault/static`, or run SvelteKit separately.

Run the repository checks with:

```sh
uv run --extra dev ruff format --check .
uv run --extra dev ruff check .
uv run --extra dev pytest -q
pnpm --dir web format
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
```

The backend does not currently configure a static type checker. Ruff formatting and linting plus the
backend test suite are the configured backend quality checks.

## Architecture

- `core/app.py` composes the FastAPI application, configures its lifecycle, and registers route groups from `routes/`. `core/` owns settings, logging, diagnostics, shared types, authentication, and API request/response models. Route modules own HTTP parsing and response contracts.
- `workers/worker.py` claims leased jobs and coordinates processing. Its data flow is `assets/jobs` in SQLite -> media conversion and diarization -> persisted transcript chunks -> transcript and visual exports -> completion state -> optional summary and speaker-name updates. `workers/worker_media.py`, `workers/worker_transcription.py`, `workers/worker_exports.py`, and `workers/worker_completion.py` own the stage boundaries.
- `persistence/` owns SQLite connection, schema, and repositories. `processing/` owns media, diarization, transcription, visual detection, export rendering, summary, and content analysis. `services/` owns upload-session coordination, media-stream process handling, and speaker operations. `web/src/lib/api-endpoints.ts` owns ordinary frontend endpoint wrappers; `web/src/lib/api/uploads.ts` owns upload-session and chunk transfer behavior.
- `web/src/routes/` contains SvelteKit pages. The asset-detail page composes media, transcript, foldout, summary, and speaker components; playback navigation is isolated in its controller. Shared API types and UI utilities are in `web/src/lib/`.

## Notes

The first version keeps Senko as an external dependency and wraps it behind `DiarizerManager`. Future memory optimization work should happen behind that wrapper:

1. Keep one warm `Diarizer` instance per process.
2. Add a Senko path that batches fbank extraction and embedding generation.
3. Accumulate embeddings and keep final global clustering.

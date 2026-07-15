# STT Vault

Private speech-to-text vault for speaker-aware transcripts.

STT Vault combines:

- Senko speaker diarization
- OpenAI audio transcription
- Persistent speaker identity matching by centroid similarity
- A single-port web UI and API
- Export formats for JSON, Whisper-like JSON, AI-readable text, SRT, VTT, RTTM, and Hyperaudio-style HTML

## Quick Start

Use the published GHCR image for normal deployment:

```sh
mkdir -p ./data
docker compose pull
docker compose up -d
```

Open `http://localhost:8080`.

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
- `JWT_ACCESS_TOKEN_MINUTES`: access token lifetime, default `60`

All protected API endpoints require `Authorization: Bearer <access-token>`. Obtain an
access token through `POST /api/auth/token` with the configured administrator password.

## Runtime Checks

```sh
docker compose ps
curl -fsS http://localhost:${APP_PORT:-8080}/api/health
```

## Development

```sh
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cd web
pnpm install
pnpm build
cd ..
uvicorn stt_vault.app:create_app --factory --reload
```

The frontend build is copied into `src/stt_vault/static` by Docker. During local development, run `pnpm build` in `web/` and copy `web/build` to `src/stt_vault/static`, or run SvelteKit separately.

## Notes

The first version keeps Senko as an external dependency and wraps it behind `DiarizerManager`. Future memory optimization work should happen behind that wrapper:

1. Keep one warm `Diarizer` instance per process.
2. Add a Senko path that batches fbank extraction and embedding generation.
3. Accumulate embeddings and keep final global clustering.

# STT Vault

Private speech-to-text vault for speaker-aware transcripts.

STT Vault combines:

- Senko speaker diarization
- OpenAI audio transcription
- Persistent speaker identity matching by centroid similarity
- A single-port web UI and API
- Export formats for JSON, Whisper-like JSON, SRT, VTT, RTTM, and Hyperaudio-style HTML

## Quick Start

```sh
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8080`.

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
- `SPEAKER_SIMILARITY_THRESHOLD`: centroid similarity threshold for speaker identity matching
- `ADMIN_PASSWORD`: optional API write protection password

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


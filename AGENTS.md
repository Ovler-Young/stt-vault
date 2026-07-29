# Repository Instructions

## Project checks

Backend supports Python >=3.11,<3.14 with `uv`. Run:

```sh
uv run --extra dev ruff format --check .
uv run --extra dev ruff check .
uv run --extra dev pytest -q
```

Frontend lives in `web/`, uses pnpm 11 and Node 22, and is checked with:

```sh
pnpm --dir web install --frozen-lockfile
pnpm --dir web format
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
```

These commands match `pyproject.toml`, `web/package.json`, and the Docker CI
workflow in `.github/workflows/docker.yml`.

## Boundaries and conventions

- `src/stt_vault/core/` owns settings, logging, diagnostics, shared types,
  authentication, and API models. `src/stt_vault/routes/` owns HTTP parsing and
  response contracts.
- `src/stt_vault/persistence/` is the SQLite boundary for connections, schema,
  and repositories. `processing/` owns media and transcript processing;
  `services/` owns upload, stream, and speaker coordination.
- `src/stt_vault/workers/` owns job orchestration and stage behavior.
- `web/src/routes/` contains SvelteKit pages; `web/src/lib/api/` contains
  frontend API wrappers and upload transfer behavior.
- Backend quality checks are Ruff and pytest; no static type checker is
  configured. Match existing TypeScript, Svelte, Python, and Ruff formatting.

## Documentation namespace

This repository's project documentation namespace is `stt-vault`.
Store project documentation under `/Documents/pages/stt-vault/`.

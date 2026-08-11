"""Authenticated v1 gateway for one persistent whisper.cpp CPU engine."""

import asyncio
import hashlib
import hmac
import io
import os
import re
import tempfile
import threading
import wave
from collections.abc import Callable
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .contracts import (
    CONTRACT_VERSION_V1,
    ModCapabilitiesV1,
    ModErrorV1,
    ModIdentityV1,
    ModLiveResponseV1,
    ModReadyResponseV1,
    TranscriptionRequestV1,
    TranscriptionResponseV1,
)

DEFAULT_MAX_AUDIO_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_AUDIO_SECONDS = 120.0


class WhisperEngine(Protocol):
    """The production adapter and test doubles share this narrow boundary."""

    identity: dict[str, str]
    readiness: str
    pid: int | None
    generation: int
    load_count: int

    def load(self) -> None: ...

    def transcribe(self, audio_path: Path, request: dict[str, object]) -> dict[str, object]: ...

    def cancel_active(self) -> None: ...

    def restart(self) -> None: ...

    def close(self) -> None: ...


class GatewayError(Exception):
    def __init__(self, status: int, category: str, message: str, retryable: bool) -> None:
        self.status = status
        self.category = category
        self.message = message
        self.retryable = retryable


def _model_dump(value: object) -> dict[str, object]:
    return value.model_dump(mode="json", exclude_none=True)  # type: ignore[union-attr]


def create_app(
    *,
    engine: WhisperEngine,
    token_path: Path,
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
    max_audio_seconds: float = DEFAULT_MAX_AUDIO_SECONDS,
    mod_id: str = "mod-whisper-cpu",
    mod_version: str = "0.1.0",
    image_digest: str | None = None,
) -> FastAPI:
    """Build a sidecar gateway without retaining uploaded audio or request state on disk."""
    if max_audio_bytes < 1 or max_audio_seconds <= 0:
        raise ValueError("audio limits must be positive")
    if (
        image_digest is None
        or re.fullmatch(r"sha256:[a-f0-9]{64}", image_digest) is None
        or image_digest == "sha256:" + "0" * 64
    ):
        raise ValueError("image digest must be a non-placeholder sha256 digest")
    model = dict(engine.identity)
    mod = ModIdentityV1.model_validate(
        {
            "id": mod_id,
            "version": mod_version,
            "image_digest": image_digest,
            "runtime": "whisper.cpp-cpu",
            "model": model,
        }
    )
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    replay: dict[UUID, tuple[str, dict[str, object]]] = {}
    cancelled: set[UUID] = set()
    inference_lock = threading.Lock()
    state_lock = threading.Lock()
    active: dict[str, UUID | threading.Event | None] = {"key": None, "done": None}
    pending: dict[UUID, threading.Event] = {}
    temporary_paths: set[Path] = set()
    temporary_paths_lock = threading.Lock()
    loaded = {"value": False}

    def error(
        status: int,
        category: str,
        message: str,
        retryable: bool,
        correlation_id: UUID | None = None,
    ) -> JSONResponse:
        payload = ModErrorV1.model_validate(
            {
                "contract_version": CONTRACT_VERSION_V1,
                "correlation_id": correlation_id or uuid4(),
                "mod": mod,
                "error": {"category": category, "message": message, "retryable": retryable},
            }
        )
        return JSONResponse(status_code=status, content=_model_dump(payload))

    def authenticate(request: Request) -> JSONResponse | None:
        header = request.headers.get("Authorization")
        expected = token_path.read_text(encoding="utf-8").strip() if token_path.is_file() else ""
        candidate = (
            header.removeprefix("Bearer ") if header and header.startswith("Bearer ") else ""
        )
        if not expected or not candidate or not hmac.compare_digest(candidate, expected):
            return error(401, "invalid_request", "mod authentication failed", False)
        return None

    @app.middleware("http")
    async def require_mod_token(request: Request, call_next: Callable) -> Response:
        if request.url.path != "/livez":
            authentication_error = authenticate(request)
            if authentication_error is not None:
                return authentication_error
        return await call_next(request)

    @app.on_event("startup")
    async def start_engine() -> None:
        if engine.readiness == "loading":
            try:
                engine.load()
                loaded["value"] = True
            except Exception:
                engine.readiness = "failed"

    @app.get("/livez")
    async def livez() -> dict[str, str]:
        return _model_dump(ModLiveResponseV1(status="live"))

    @app.get("/readyz")
    async def readyz() -> Response:
        if engine.readiness == "loading":
            return error(503, "not_ready", "model is loading", True)
        if engine.readiness != "ready":
            return error(503, "provider_failure", "model failed to load", False)
        payload = ModReadyResponseV1.model_validate(
            {
                "status": "ready",
                "model": {key: model[key] for key in ("id", "revision", "sha256")},
                "rss_mb": _rss_mb(),
            }
        )
        return JSONResponse(content=_model_dump(payload))

    @app.get("/v1/capabilities")
    async def capabilities() -> Response:
        payload = ModCapabilitiesV1.model_validate(
            {
                "contract_version": CONTRACT_VERSION_V1,
                "correlation_id": uuid4(),
                "mod": mod,
                "result": {
                    "offerings": [{"model_id": model["id"], "device_id": "cpu"}],
                    "max_audio_bytes": max_audio_bytes,
                    "max_audio_seconds": max_audio_seconds,
                    "readiness": engine.readiness,
                },
            }
        )
        return JSONResponse(content=_model_dump(payload))

    @app.post("/v1/cancellations/{idempotency_key}", status_code=204)
    async def cancel(idempotency_key: UUID) -> Response:
        with state_lock:
            if idempotency_key in replay:
                return Response(status_code=204)
            cancelled.add(idempotency_key)
            is_active = active["key"] == idempotency_key
            completed = active["done"] if is_active else pending.get(idempotency_key)
        if is_active:
            engine.readiness = "loading"
            await asyncio.to_thread(engine.cancel_active)
            if isinstance(completed, threading.Event):
                await asyncio.to_thread(completed.wait)
            threading.Thread(target=_restart_engine, args=(engine,), daemon=True).start()
        elif isinstance(completed, threading.Event):
            await asyncio.to_thread(completed.wait)
        return Response(status_code=204)

    @app.post("/v1/transcriptions")
    async def transcribe(request: Request) -> Response:
        correlation_id = uuid4()
        temporary_path: Path | None = None
        try:
            body = await request.body()
            if len(body) > max_audio_bytes:
                raise GatewayError(
                    413, "resource_exhausted", "request exceeds the configured size limit", True
                )
            request_part, audio = _multipart_parts(request.headers.get("content-type"), body)
            try:
                parsed_request = TranscriptionRequestV1.model_validate_json(
                    request_part.decode("utf-8")
                )
            except ValidationError as validation_error:
                raise GatewayError(
                    422, "invalid_request", "request body failed validation", False
                ) from validation_error
            correlation_id = parsed_request.correlation_id
            duration = _wav_duration(audio)
            if duration > max_audio_seconds:
                raise GatewayError(
                    413, "resource_exhausted", "audio exceeds the configured duration limit", True
                )
            planned_duration = parsed_request.chunk.end - parsed_request.chunk.start
            if abs(duration - planned_duration) > 0.050:
                raise GatewayError(
                    422, "invalid_request", "audio duration did not match the planned chunk", False
                )
            request_hash = hashlib.sha256(
                parsed_request.model_dump_json().encode() + audio
            ).hexdigest()
            completed = threading.Event()
            with state_lock:
                if parsed_request.idempotency_key in cancelled:
                    raise GatewayError(503, "provider_failure", "request was cancelled", False)
                pending[parsed_request.idempotency_key] = completed
            response_body = await asyncio.to_thread(
                _run_transcription,
                engine,
                parsed_request,
                audio,
                request_hash,
                planned_duration,
                mod,
                replay,
                cancelled,
                inference_lock,
                state_lock,
                active,
                pending,
                completed,
                loaded,
                temporary_paths,
                temporary_paths_lock,
            )
            return JSONResponse(content=response_body, headers=_engine_headers(engine))
        except GatewayError as gateway_error:
            return error(
                gateway_error.status,
                gateway_error.category,
                gateway_error.message,
                gateway_error.retryable,
                correlation_id,
            )
        except (OSError, wave.Error, ValidationError, ValueError):
            return error(
                422, "invalid_request", "audio or response failed validation", False, correlation_id
            )
        except Exception:
            return error(
                503, "provider_failure", "transcription engine failed", False, correlation_id
            )
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
                with temporary_paths_lock:
                    temporary_paths.discard(temporary_path)

    @app.on_event("shutdown")
    async def stop_engine() -> None:
        close = getattr(engine, "close", None)
        if callable(close):
            close()
        with temporary_paths_lock:
            paths = tuple(temporary_paths)
            temporary_paths.clear()
        for path in paths:
            path.unlink(missing_ok=True)

    return app


def _restart_engine(engine: WhisperEngine) -> None:
    try:
        engine.restart()
    except Exception:
        engine.readiness = "failed"


def _run_transcription(
    engine: WhisperEngine,
    parsed_request: TranscriptionRequestV1,
    audio: bytes,
    request_hash: str,
    planned_duration: float,
    mod: ModIdentityV1,
    replay: dict[UUID, tuple[str, dict[str, object]]],
    cancelled: set[UUID],
    inference_lock: threading.Lock,
    state_lock: threading.Lock,
    active: dict[str, UUID | threading.Event | None],
    pending: dict[UUID, threading.Event],
    completed: threading.Event,
    loaded: dict[str, bool],
    temporary_paths: set[Path],
    temporary_paths_lock: threading.Lock,
) -> dict[str, object]:
    """Run one inference off the event loop; whisper-server accepts one request at a time."""
    temporary_path: Path | None = None
    try:
        with inference_lock:
            with state_lock:
                if parsed_request.idempotency_key in cancelled:
                    raise GatewayError(503, "provider_failure", "request was cancelled", False)
                existing = replay.get(parsed_request.idempotency_key)
                if existing is not None:
                    if existing[0] != request_hash:
                        raise GatewayError(
                            422, "invalid_request", "idempotency key request mismatch", False
                        )
                    return existing[1]
            if not loaded["value"]:
                engine.load()
                loaded["value"] = True
            if engine.readiness != "ready":
                raise GatewayError(503, "provider_failure", "model is unavailable", False)
            with state_lock:
                if parsed_request.idempotency_key in cancelled:
                    raise GatewayError(503, "provider_failure", "request was cancelled", False)
                active["key"] = parsed_request.idempotency_key
                active["done"] = completed
            with tempfile.NamedTemporaryFile(
                prefix="stt-whisper-", suffix=".wav", delete=False
            ) as temporary:
                temporary.write(audio)
                temporary_path = Path(temporary.name)
            with temporary_paths_lock:
                temporary_paths.add(temporary_path)
            result = engine.transcribe(temporary_path, parsed_request.model_dump(mode="json"))
            with state_lock:
                if parsed_request.idempotency_key in cancelled:
                    raise GatewayError(503, "provider_failure", "request was cancelled", False)
            response = TranscriptionResponseV1.model_validate(
                {
                    "contract_version": CONTRACT_VERSION_V1,
                    "correlation_id": parsed_request.correlation_id,
                    "mod": mod,
                    "result": result,
                },
                context={"chunk_duration": planned_duration},
            )
            response_body = _model_dump(response)
            with state_lock:
                replay[parsed_request.idempotency_key] = (request_hash, response_body)
            return response_body
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
            with temporary_paths_lock:
                temporary_paths.discard(temporary_path)
        with state_lock:
            if active["key"] == parsed_request.idempotency_key:
                active["key"] = None
                active["done"] = None
            if pending.get(parsed_request.idempotency_key) is completed:
                pending.pop(parsed_request.idempotency_key)
            completed.set()


def _wav_duration(audio: bytes) -> float:
    with wave.open(io.BytesIO(audio), "rb") as wav:
        if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError("audio must be PCM16 mono WAV")
        if wav.getframerate() != 16_000:
            raise ValueError("audio must be 16 kHz WAV")
        return wav.getnframes() / wav.getframerate()


def _multipart_parts(content_type: str | None, body: bytes) -> tuple[bytes, bytes]:
    if content_type is None or not content_type.startswith("multipart/form-data"):
        raise GatewayError(
            422, "invalid_request", "request body must be multipart form data", False
        )
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    if not message.is_multipart():
        raise GatewayError(
            422, "invalid_request", "request body must be multipart form data", False
        )
    parts: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        if (
            part.get_content_disposition() != "form-data"
            or not isinstance(name, str)
            or name in parts
            or payload is None
        ):
            raise GatewayError(
                422, "invalid_request", "request requires request and audio parts", False
            )
        parts[name] = (part.get_content_type(), payload)
    if set(parts) != {"request", "audio"}:
        raise GatewayError(
            422, "invalid_request", "request requires request and audio parts", False
        )
    request_part, audio_part = parts["request"], parts["audio"]
    if request_part[0] != "application/json" or audio_part[0] != "audio/wav":
        raise GatewayError(
            422, "invalid_request", "request parts have unsupported media types", False
        )
    return request_part[1], audio_part[1]


def _rss_mb() -> float:
    try:
        pages = os.sysconf("SC_PAGE_SIZE")
        resident = int(Path("/proc/self/statm").read_text(encoding="utf-8").split()[1])
        return pages * resident / (1024 * 1024)
    except (IndexError, OSError, ValueError):
        return 0.0


def _engine_headers(engine: WhisperEngine) -> dict[str, str]:
    return {
        "X-Mod-Engine-Pid": str(engine.pid or 0),
        "X-Mod-Engine-Generation": str(engine.generation),
        "X-Mod-Engine-Load-Count": str(engine.load_count),
    }

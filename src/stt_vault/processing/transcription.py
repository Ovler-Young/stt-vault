import hashlib
import http.client
import json
import math
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from openai import OpenAI
from pydantic import ValidationError

from stt_vault.core.models.mod_contracts import (
    ModCapabilitiesV1,
    ModErrorV1,
    ModReadyResponseV1,
    TranscriptionRequestV1,
    TranscriptionResponseV1,
)
from stt_vault.core.models.records import (
    SpeakerSegment,
    TimedTranscriptUnit,
    TranscriptChunk,
    TranscriptSegment,
)

from .media_transcoding import extract_audio_chunk

ChunkExtractor = Callable[[Path, Path, float, float], Path]

SIDECAR_TRANSCRIPTION_BASE_URL = "http://mod-whisper-cpu:8081"
SIDECAR_TOKEN_PATH = Path("/run/secrets/stt_mod_token")
SIDECAR_CONNECT_TIMEOUT_SECONDS = 2.0
SIDECAR_RESPONSE_TIMEOUT_SECONDS = 90.0
SIDECAR_ATTEMPT_TIMEOUT_SECONDS = 95.0


@dataclass(frozen=True)
class SidecarHttpResponse:
    status: int
    body: bytes


class SidecarHttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        connect_timeout: float,
        response_timeout: float,
        total_timeout: float,
    ) -> SidecarHttpResponse: ...

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        connect_timeout: float,
        response_timeout: float,
        total_timeout: float,
    ) -> SidecarHttpResponse: ...


class SidecarProviderError(RuntimeError):
    def __init__(self, category: str, message: str, *, retryable: bool) -> None:
        super().__init__(f"{category}: {message}")
        self.category = category
        self.retryable = retryable


class _SidecarCompletionError(RuntimeError):
    """Keep a persistence failure from changing an accepted provider invocation."""


@dataclass(frozen=True)
class SidecarRequestIdentity:
    idempotency_key: str
    correlation_id: str


@dataclass(frozen=True)
class SidecarTranscriptionResult:
    text: str
    provider_metadata: dict[str, str]
    timing_ms: int
    timed_units: tuple[TimedTranscriptUnit, ...] = ()


class SidecarInvocationLifecycle(Protocol):
    def sent(self) -> bool: ...

    def accepted(
        self, provider_metadata: Mapping[str, str] | None = None, timing_ms: int | None = None
    ) -> bool: ...

    def retry(self, error: Exception) -> SidecarRequestIdentity | None: ...

    def completed(self) -> bool: ...

    def failed(self, error: Exception) -> bool: ...


@dataclass(frozen=True)
class SidecarPreparedRequest:
    audio_path: Path
    request_hash: str
    identity: SidecarRequestIdentity
    lifecycle: SidecarInvocationLifecycle | None = None
    on_completed: Callable[[TranscriptSegment, tuple[TimedTranscriptUnit, ...]], None] | None = None


def canonical_sidecar_request_hash(
    *,
    asset_id: str,
    chunk: TranscriptChunk,
    idempotency_key: str,
    prompt: str | None,
    audio_path: Path,
) -> str:
    """Hash the immutable request fields and extracted WAV before sidecar I/O."""
    request = {
        "asset_id": asset_id,
        "chunk": {
            "end": chunk.end,
            "index": chunk.chunk_index,
            "speaker_id": chunk.speaker,
            "start": chunk.start,
        },
        "contract_version": "v1",
        "idempotency_key": idempotency_key,
        "language": None,
        "prompt": prompt,
    }
    digest = hashlib.sha256()
    digest.update(json.dumps(request, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    digest.update(b"\0")
    with audio_path.open("rb") as audio_file:
        for block in iter(lambda: audio_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class HttpClientSidecarTransport:
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        connect_timeout: float,
        response_timeout: float,
        total_timeout: float,
    ) -> SidecarHttpResponse:
        return self._request(
            "GET", url, headers, b"", connect_timeout, response_timeout, total_timeout
        )

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        connect_timeout: float,
        response_timeout: float,
        total_timeout: float,
    ) -> SidecarHttpResponse:
        return self._request(
            "POST", url, headers, body, connect_timeout, response_timeout, total_timeout
        )

    @staticmethod
    def _request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        connect_timeout: float,
        response_timeout: float,
        total_timeout: float,
    ) -> SidecarHttpResponse:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("sidecar URL must be an absolute HTTP URL")
        connection_type = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        deadline = time.monotonic() + total_timeout
        connection = connection_type(parsed.netloc, timeout=min(connect_timeout, total_timeout))
        try:
            connection.request(method, parsed.path or "/", body=body, headers=dict(headers))
            if connection.sock is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                connection.sock.settimeout(min(response_timeout, remaining))
            response = connection.getresponse()
            if connection.sock is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                connection.sock.settimeout(min(response_timeout, remaining))
            return SidecarHttpResponse(status=response.status, body=response.read())
        except (TimeoutError, OSError, http.client.HTTPException) as error:
            raise SidecarProviderError(
                "unavailable", "sidecar request failed", retryable=True
            ) from error
        finally:
            connection.close()


class SidecarTranscriptionClient:
    def __init__(
        self,
        base_url: str = SIDECAR_TRANSCRIPTION_BASE_URL,
        token: str | None = None,
        transport: SidecarHttpTransport | None = None,
        *,
        token_path: Path = SIDECAR_TOKEN_PATH,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else token_path.read_text(encoding="utf-8").strip()
        if not self.token:
            raise ValueError("sidecar bearer token must be nonempty")
        self.transport = transport or HttpClientSidecarTransport()

    def validate_startup(self, *, expected_id: str, expected_digest: str) -> None:
        self._validate_attempt_preflight(
            expected_id=expected_id,
            expected_digest=expected_digest,
            deadline=time.monotonic() + SIDECAR_ATTEMPT_TIMEOUT_SECONDS,
        )

    def _validate_attempt_preflight(
        self,
        *,
        expected_id: str | None = None,
        expected_digest: str | None = None,
        deadline: float,
    ) -> ModCapabilitiesV1:
        capabilities = self._get_contract_response(
            "/v1/capabilities", ModCapabilitiesV1, deadline=deadline
        )
        if expected_id is not None and capabilities.mod.id != expected_id:
            raise SidecarProviderError(
                "contract_incompatible",
                "sidecar identity did not match selected provider",
                retryable=False,
            )
        if expected_digest is not None and capabilities.mod.image_digest != expected_digest:
            raise SidecarProviderError(
                "contract_incompatible",
                "sidecar identity did not match selected provider",
                retryable=False,
            )
        if (
            capabilities.result.readiness != "ready"
            or capabilities.result.max_audio_bytes < 25 * 1024 * 1024
            or capabilities.result.max_audio_seconds < 120
        ):
            raise SidecarProviderError(
                "not_ready", "sidecar capabilities are not ready", retryable=True
            )
        ready = self._get_contract_response("/readyz", ModReadyResponseV1, deadline=deadline)
        if ready.model.model_dump() != capabilities.mod.model.model_dump(
            include={"id", "revision", "sha256"}
        ):
            raise SidecarProviderError(
                "contract_incompatible",
                "sidecar readiness model did not match capabilities",
                retryable=False,
            )
        return capabilities

    def _get_contract_response(
        self,
        path: str,
        model: type[ModCapabilitiesV1] | type[ModReadyResponseV1],
        *,
        deadline: float,
    ) -> ModCapabilitiesV1 | ModReadyResponseV1:
        total_timeout = self._remaining_timeout(deadline)
        try:
            response = self.transport.get(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.token}"},
                connect_timeout=min(SIDECAR_CONNECT_TIMEOUT_SECONDS, total_timeout),
                response_timeout=min(SIDECAR_RESPONSE_TIMEOUT_SECONDS, total_timeout),
                total_timeout=total_timeout,
            )
        except (TimeoutError, OSError) as error:
            raise SidecarProviderError(
                "unavailable", "sidecar health request failed", retryable=True
            ) from error
        if response.status < 200 or response.status >= 300:
            self._raise_error_response(response)
        try:
            return model.model_validate(json.loads(response.body))
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
            raise SidecarProviderError(
                "contract_incompatible",
                "sidecar health response failed contract validation",
                retryable=False,
            ) from error

    @staticmethod
    def _remaining_timeout(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SidecarProviderError(
                "unavailable", "sidecar attempt deadline exceeded", retryable=True
            )
        return remaining

    def transcribe(
        self,
        *,
        asset_id: str,
        chunk: TranscriptChunk,
        audio_path: Path,
        idempotency_key: str,
        correlation_id: str,
        prompt: str | None,
    ) -> SidecarTranscriptionResult:
        deadline = time.monotonic() + SIDECAR_ATTEMPT_TIMEOUT_SECONDS
        request = TranscriptionRequestV1.model_validate(
            {
                "contract_version": "v1",
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key,
                "asset_id": asset_id,
                "chunk": {
                    "index": chunk.chunk_index,
                    "start": chunk.start,
                    "end": chunk.end,
                    "speaker_id": chunk.speaker,
                },
                "language": None,
                "prompt": prompt,
            }
        )
        body, content_type = self._multipart_body(request, audio_path)
        started_at = time.monotonic()
        capabilities = self._validate_attempt_preflight(deadline=deadline)
        total_timeout = self._remaining_timeout(deadline)
        try:
            response = self.transport.post(
                f"{self.base_url}/v1/transcriptions",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                },
                body=body,
                connect_timeout=min(SIDECAR_CONNECT_TIMEOUT_SECONDS, total_timeout),
                response_timeout=min(SIDECAR_RESPONSE_TIMEOUT_SECONDS, total_timeout),
                total_timeout=total_timeout,
            )
        except SidecarProviderError:
            raise
        except (TimeoutError, OSError) as error:
            raise SidecarProviderError(
                "unavailable", "sidecar request failed", retryable=True
            ) from error
        if response.status < 200 or response.status >= 300:
            self._raise_error_response(response)
        try:
            payload = json.loads(response.body)
            parsed = TranscriptionResponseV1.model_validate(
                payload,
                context={
                    "chunk_duration": chunk.end - chunk.start,
                    "timed_units_capability": (
                        capabilities.result.transcription.timed_units
                        if capabilities.result.transcription is not None
                        else None
                    ),
                },
            )
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
            raise SidecarProviderError(
                "contract_incompatible",
                "sidecar response failed contract validation",
                retryable=False,
            ) from error
        chunk_offset_ms = math.floor(chunk.start * 1000 + 0.5)
        return SidecarTranscriptionResult(
            text=" ".join(segment.text.strip() for segment in parsed.result.segments),
            provider_metadata={
                "mod_id": parsed.mod.id,
                "mod_version": parsed.mod.version,
                "mod_image_digest": parsed.mod.image_digest,
                "runtime": parsed.mod.runtime,
                "model_id": parsed.mod.model.id,
                "model_revision": parsed.mod.model.revision,
                "model_sha256": parsed.mod.model.sha256,
                "license_ref": parsed.mod.model.license_ref,
                "access_declaration": parsed.mod.model.access_declaration,
            },
            timing_ms=round((time.monotonic() - started_at) * 1000),
            timed_units=tuple(
                TimedTranscriptUnit(
                    unit.unit_index,
                    unit.text,
                    chunk_offset_ms + unit.start_ms,
                    chunk_offset_ms + unit.end_ms,
                    unit.confidence,
                    unit.language,
                    unit.token_kind,
                )
                for unit in parsed.result.timed_units or ()
            ),
        )

    def cancel(self, idempotency_key: str) -> int:
        try:
            response = self.transport.post(
                f"{self.base_url}/v1/cancellations/{idempotency_key}",
                headers={"Authorization": f"Bearer {self.token}", "Content-Length": "0"},
                body=b"",
                connect_timeout=SIDECAR_CONNECT_TIMEOUT_SECONDS,
                response_timeout=SIDECAR_RESPONSE_TIMEOUT_SECONDS,
                total_timeout=SIDECAR_ATTEMPT_TIMEOUT_SECONDS,
            )
        except SidecarProviderError:
            raise
        except (TimeoutError, OSError) as error:
            raise SidecarProviderError(
                "unavailable", "sidecar cancellation request failed", retryable=True
            ) from error
        if response.status != 204:
            self._raise_error_response(response)
        return response.status

    def _raise_error_response(self, response: SidecarHttpResponse) -> None:
        try:
            payload = json.loads(response.body)
            error = ModErrorV1.model_validate(payload).error
        except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as parse_error:
            raise SidecarProviderError(
                "contract_incompatible",
                "sidecar error response failed contract validation",
                retryable=False,
            ) from parse_error
        raise SidecarProviderError(error.category, error.message, retryable=error.retryable)

    @staticmethod
    def _multipart_body(request: TranscriptionRequestV1, audio_path: Path) -> tuple[bytes, str]:
        boundary = f"stt-vault-{uuid4().hex}"
        request_json = request.model_dump_json().encode("utf-8")
        audio = audio_path.read_bytes()
        parts = [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="request"\r\n',
            b"Content-Type: application/json\r\n\r\n",
            request_json,
            b"\r\n",
            (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="audio"; filename="{audio_path.name}"\r\n'
            ).encode(),
            b"Content-Type: audio/wav\r\n\r\n",
            audio,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class TranscriptionResponse(Protocol):
    text: str | None


@dataclass(frozen=True)
class _NormalizedTranscriptionResponse:
    text: str | None


class AudioTranscriptions(Protocol):
    def create(
        self,
        *,
        file: BinaryIO,
        model: str,
        prompt: str | None = None,
    ) -> TranscriptionResponse: ...


class TranscriptionAudio(Protocol):
    transcriptions: AudioTranscriptions


class TranscriptionClient(Protocol):
    audio: TranscriptionAudio


class TranscriptionClientFactory(Protocol):
    def __call__(self, *, api_key: str, base_url: str) -> TranscriptionClient: ...


class Clock(Protocol):
    def __call__(self) -> float: ...


class Sleeper(Protocol):
    def __call__(self, seconds: float) -> None: ...


class ChunkDoneCallback(Protocol):
    def __call__(self, index: int, result: TranscriptSegment) -> None: ...


class ChunkRetryCallback(Protocol):
    def __call__(self, index: int, attempt: int, error: Exception, retry_at: int) -> None: ...


def _normalize_transcription_response(response: object) -> TranscriptionResponse:
    if isinstance(response, Mapping):
        if "text" not in response:
            raise TypeError("transcription response mapping is missing text")
        text = response["text"]
    else:
        missing = object()
        text = getattr(response, "text", missing)
        if text is missing:
            raise TypeError("transcription response is missing text")
    if text is not None and not isinstance(text, str):
        raise TypeError("transcription response text must be a string or null")
    return _NormalizedTranscriptionResponse(text=text)


def build_chunks(
    segments: list[SpeakerSegment],
    *,
    max_seconds: float,
) -> list[TranscriptChunk]:
    chunks: list[TranscriptChunk] = []
    chunk_index = 0
    for segment in segments:
        start = segment.start
        end = segment.end
        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + max_seconds)
            chunks.append(TranscriptChunk(cursor, chunk_end, segment.speaker, chunk_index))
            chunk_index += 1
            cursor = chunk_end
    return chunks


def build_transcription_plan(
    raw_segments: list[SpeakerSegment], *, max_seconds: float
) -> list[TranscriptChunk]:
    return build_chunks(raw_segments, max_seconds=max_seconds)


def transcript_chunks_match_plan(
    existing_chunks: list[TranscriptSegment], chunks: list[TranscriptChunk]
) -> bool:
    for existing in existing_chunks:
        index = existing.chunk_index
        if index is None or index < 0 or index >= len(chunks):
            return False
        expected = chunks[index]
        if existing.speaker != expected.speaker:
            return False
        for actual, expected_value in (
            (existing.start, expected.start),
            (existing.end, expected.end),
            (existing.chunk_start, expected.start),
            (existing.chunk_end, expected.end),
        ):
            if actual is None or abs(actual - expected_value) > 0.001:
                return False
    return True


class Transcriber:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        prompt: str,
        concurrency: int,
        retry_seconds: int,
        max_retries: int,
        retry_backoff_seconds: list[int] | None = None,
        on_chunk_done: ChunkDoneCallback | None = None,
        on_chunk_retry: ChunkRetryCallback | None = None,
        client: TranscriptionClient | None = None,
        client_factory: TranscriptionClientFactory = OpenAI,
        chunk_extractor: ChunkExtractor = extract_audio_chunk,
        clock: Clock = time.time,
        sleeper: Sleeper = time.sleep,
        provider: Literal["openai", "mod-whisper-cpu"] = "openai",
        sidecar_client: SidecarTranscriptionClient | None = None,
    ) -> None:
        if provider not in {"openai", "mod-whisper-cpu"}:
            raise ValueError(f"unsupported transcription provider: {provider}")
        if provider == "openai":
            self.client = client or client_factory(api_key=api_key, base_url=base_url)
        elif client is not None:
            raise ValueError("OpenAI client cannot be used with the selected sidecar provider")
        else:
            self.client = None
        self.provider = provider
        self.sidecar_client = sidecar_client
        self.chunk_extractor = chunk_extractor
        self.model = model
        self.prompt = prompt
        self.concurrency = max(1, concurrency)
        self.retry_seconds = max(1, retry_seconds)
        self.max_retries = max(1, max_retries)
        self.retry_backoff_seconds = retry_backoff_seconds or [self.retry_seconds]
        self.on_chunk_done = on_chunk_done
        self.on_chunk_retry = on_chunk_retry
        self.clock = clock
        self.sleeper = sleeper
        self._pause_lock = threading.Lock()
        self._resume_at = 0.0

    def transcribe_chunks(
        self,
        media_path: Path,
        chunks: list[TranscriptChunk],
        tmp_dir: Path,
        *,
        asset_id: str | None = None,
        sidecar_request_identities: Mapping[int, SidecarRequestIdentity] | None = None,
        sidecar_prepared_requests: Mapping[int, SidecarPreparedRequest] | None = None,
    ) -> list[TranscriptSegment]:
        if not chunks:
            return []

        results: list[TranscriptSegment] = []
        chunk_iter = iter(enumerate(chunks))
        pending: set[Future[TranscriptSegment]] = set()

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            while True:
                self._wait_for_pause()
                while len(pending) < self.concurrency:
                    try:
                        index, chunk = next(chunk_iter)
                    except StopIteration:
                        break
                    pending.add(
                        executor.submit(
                            self._transcribe_one,
                            media_path,
                            chunk,
                            tmp_dir,
                            chunk.chunk_index,
                            asset_id,
                            (sidecar_request_identities or {}).get(chunk.chunk_index),
                            (sidecar_prepared_requests or {}).get(chunk.chunk_index),
                        )
                    )

                if not pending:
                    break

                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    results.append(future.result())

        return sorted(results, key=lambda item: (item.start, item.end))

    def _transcribe_one(
        self,
        media_path: Path,
        chunk: TranscriptChunk,
        tmp_dir: Path,
        index: int,
        asset_id: str | None,
        sidecar_request_identity: SidecarRequestIdentity | None,
        sidecar_prepared_request: SidecarPreparedRequest | None,
    ) -> TranscriptSegment:
        chunk_path = (
            sidecar_prepared_request.audio_path
            if sidecar_prepared_request is not None
            else tmp_dir / f"chunk-{index:06d}.wav"
        )
        request_identity = (
            sidecar_prepared_request.identity
            if sidecar_prepared_request is not None
            else sidecar_request_identity
        ) or SidecarRequestIdentity(idempotency_key=str(uuid4()), correlation_id=str(uuid4()))
        try:
            for attempt in range(1, max(self.max_retries, 3) + 1):
                try:
                    result, timed_units = self._transcribe_attempt(
                        media_path,
                        chunk,
                        chunk_path,
                        attempt,
                        asset_id,
                        request_identity,
                        sidecar_prepared_request,
                    )
                    if (
                        sidecar_prepared_request is not None
                        and sidecar_prepared_request.on_completed
                    ):
                        try:
                            sidecar_prepared_request.on_completed(result, timed_units)
                        except Exception as error:
                            raise _SidecarCompletionError(
                                "sidecar completion persistence failed"
                            ) from error
                    elif self.on_chunk_done:
                        self.on_chunk_done(index, result)
                    if (
                        sidecar_prepared_request is not None
                        and sidecar_prepared_request.on_completed is None
                        and (lifecycle := sidecar_prepared_request.lifecycle) is not None
                        and not lifecycle.completed()
                    ):
                        raise RuntimeError(
                            "sidecar invocation claim became stale before completion"
                        )
                    return result
                except _SidecarCompletionError:
                    raise
                except Exception as exc:
                    max_attempts, retry_backoff_seconds = self._retry_policy(exc)
                    if attempt >= max_attempts:
                        if (
                            sidecar_prepared_request is not None
                            and (lifecycle := sidecar_prepared_request.lifecycle) is not None
                        ):
                            lifecycle.failed(exc)
                        raise
                    if (
                        sidecar_prepared_request is not None
                        and (lifecycle := sidecar_prepared_request.lifecycle) is not None
                        and (retry_identity := lifecycle.retry(exc)) is None
                    ):
                        raise RuntimeError(
                            "sidecar invocation claim became stale before retry"
                        ) from exc
                    if (
                        sidecar_prepared_request is not None
                        and (lifecycle := sidecar_prepared_request.lifecycle) is not None
                    ):
                        request_identity = retry_identity
                    delay = self._retry_delay(attempt, retry_backoff_seconds)
                    retry_at = int(self.clock()) + delay
                    if self.on_chunk_retry:
                        self.on_chunk_retry(index, attempt, exc, retry_at)
                    self._pause_all(delay)
        finally:
            chunk_path.unlink(missing_ok=True)

        raise RuntimeError("unreachable transcription retry state")

    def _transcribe_attempt(
        self,
        media_path: Path,
        chunk: TranscriptChunk,
        chunk_path: Path,
        attempt: int,
        asset_id: str | None,
        request_identity: SidecarRequestIdentity,
        sidecar_prepared_request: SidecarPreparedRequest | None,
    ) -> tuple[TranscriptSegment, tuple[TimedTranscriptUnit, ...]]:
        self._wait_for_pause()
        if sidecar_prepared_request is None:
            self.chunk_extractor(
                media_path,
                chunk_path,
                chunk.start,
                chunk.end,
            )

        if self.provider == "mod-whisper-cpu":
            if asset_id is None:
                raise ValueError("sidecar transcription requires an asset ID")
            if (
                sidecar_prepared_request is not None
                and (lifecycle := sidecar_prepared_request.lifecycle) is not None
                and not lifecycle.sent()
            ):
                raise RuntimeError("sidecar invocation claim became stale before request")
            client = self.sidecar_client or SidecarTranscriptionClient()
            response = client.transcribe(
                asset_id=asset_id,
                chunk=chunk,
                audio_path=chunk_path,
                idempotency_key=request_identity.idempotency_key,
                correlation_id=request_identity.correlation_id,
                prompt=self.prompt or None,
            )
            if isinstance(response, SidecarTranscriptionResult):
                text = response.text
                provider_metadata = response.provider_metadata
                timing_ms = response.timing_ms
                timed_units = response.timed_units
            else:
                text = response
                provider_metadata = None
                timing_ms = None
                timed_units = ()
            if (
                sidecar_prepared_request is not None
                and (lifecycle := sidecar_prepared_request.lifecycle) is not None
                and not (
                    lifecycle.accepted(provider_metadata, timing_ms)
                    if provider_metadata is not None
                    else lifecycle.accepted()
                )
            ):
                raise RuntimeError("sidecar invocation claim became stale after response")
        else:
            if self.client is None:
                raise RuntimeError("OpenAI transcription client is not configured")
            kwargs: dict[str, str] = {"model": self.model}
            if self.prompt:
                kwargs["prompt"] = self.prompt
            with chunk_path.open("rb") as audio_file:
                response = _normalize_transcription_response(
                    self.client.audio.transcriptions.create(file=audio_file, **kwargs)
                )
            text = response.text or ""
            timed_units = ()

        return (
            TranscriptSegment(
                start=chunk.start,
                end=chunk.end,
                chunk_start=chunk.start,
                chunk_end=chunk.end,
                speaker=chunk.speaker,
                text=text.strip(),
                attempts=attempt,
            ),
            timed_units,
        )

    @staticmethod
    def _retry_delay(attempt: int, retry_backoff_seconds: list[int]) -> int:
        index = min(max(0, attempt - 1), len(retry_backoff_seconds) - 1)
        return retry_backoff_seconds[index]

    def _retry_policy(self, error: Exception) -> tuple[int, list[int]]:
        if not isinstance(error, SidecarProviderError):
            return self.max_retries, self.retry_backoff_seconds
        if error.category == "unavailable":
            return 3, [2, 10]
        if error.category in {"not_ready", "resource_exhausted"}:
            return 3, [10, 30]
        if error.category == "provider_failure" and error.retryable:
            return 2, [10]
        return 1, []

    def _pause_all(self, delay: int) -> None:
        with self._pause_lock:
            now = self.clock()
            self._resume_at = max(self._resume_at, now + delay)
            sleep_for = max(0.0, self._resume_at - now)
        self.sleeper(sleep_for)

    def _wait_for_pause(self) -> None:
        with self._pause_lock:
            sleep_for = max(0.0, self._resume_at - self.clock())
        if sleep_for > 0:
            self.sleeper(sleep_for)

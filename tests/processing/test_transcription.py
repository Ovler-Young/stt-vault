import json
from pathlib import Path

import pytest

from stt_vault.core.models.records import (
    SpeakerSegment,
    TimedTranscriptUnit,
    TranscriptChunk,
    TranscriptSegment,
)
from stt_vault.processing.exports import to_ai_text
from stt_vault.processing.media_transcoding import extract_audio_chunk
from stt_vault.processing.transcription import (
    SidecarHttpResponse,
    SidecarPreparedRequest,
    SidecarProviderError,
    SidecarRequestIdentity,
    SidecarTranscriptionClient,
    SidecarTranscriptionResult,
    Transcriber,
    build_chunks,
    build_transcription_plan,
    canonical_sidecar_request_hash,
    transcript_chunks_match_plan,
)


def test_sidecar_client_maps_declared_timed_units_to_absolute_chunk_milliseconds(tmp_path) -> None:
    class Transport:
        def get(self, url, **_kwargs):
            if url.endswith("/v1/capabilities"):
                return _sidecar_capabilities_response(
                    timed_units={
                        "unit_kinds": ["word", "punctuation"],
                        "time_base": "chunk_ms",
                        "precision_ms": 20,
                    }
                )
            return _sidecar_ready_response()

        def post(self, *_args, **_kwargs):
            return SidecarHttpResponse(
                status=200,
                body=json.dumps(
                    _mod_response(
                        {
                            "kind": "speech",
                            "segments": [{"start": 0, "end": 1, "text": "hello,"}],
                            "timed_units": [
                                {
                                    "unit_index": 0,
                                    "text": "hello",
                                    "start_ms": 0,
                                    "end_ms": 500,
                                    "confidence": 0.9,
                                    "language": "en",
                                    "token_kind": "word",
                                },
                                {
                                    "unit_index": 1,
                                    "text": ",",
                                    "start_ms": 500,
                                    "end_ms": 500,
                                    "confidence": None,
                                    "language": "en",
                                    "token_kind": "punctuation",
                                },
                            ],
                        }
                    )
                ).encode(),
            )

    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")
    client = SidecarTranscriptionClient("http://mod-whisper-cpu:8081", "token", Transport())
    result = client.transcribe(
        asset_id="asset:1",
        chunk=TranscriptChunk(1.2345, 2.2345, "speaker:1", 3),
        audio_path=audio,
        idempotency_key="123e4567-e89b-42d3-a456-426614174001",
        correlation_id="123e4567-e89b-42d3-a456-426614174002",
        prompt=None,
    )

    actual_units = [
        (unit.text, unit.start_ms, unit.end_ms, unit.token_kind) for unit in result.timed_units
    ]
    assert actual_units == [
        ("hello", 1235, 1735, "word"),
        (",", 1735, 1735, "punctuation"),
    ]


def test_transcriber_passes_sidecar_timed_units_to_the_completion_callback(tmp_path) -> None:
    completed: list[tuple[TranscriptSegment, tuple[TimedTranscriptUnit, ...]]] = []
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")

    class Sidecar:
        def transcribe(self, **_kwargs):
            return SidecarTranscriptionResult(
                "hello",
                {},
                1,
                (TimedTranscriptUnit(0, "hello", 1235, 1735, 0.9, "en", "word"),),
            )

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=Sidecar(),
    )
    transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(1.2345, 2.2345, "speaker:1", 3)],
        tmp_path,
        asset_id="asset:1",
        sidecar_prepared_requests={
            3: SidecarPreparedRequest(
                audio,
                "a" * 64,
                SidecarRequestIdentity(
                    "123e4567-e89b-42d3-a456-426614174001",
                    "123e4567-e89b-42d3-a456-426614174002",
                ),
                on_completed=lambda segment, units: completed.append((segment, units)),
            )
        },
    )

    assert completed[0][0].text == "hello"
    assert [(unit.start_ms, unit.end_ms) for unit in completed[0][1]] == [(1235, 1735)]


@pytest.mark.parametrize(
    "timed_units",
    [
        None,
        [
            {
                "unit_index": 0,
                "text": "x",
                "start_ms": 5,
                "end_ms": 20,
                "confidence": 0.5,
                "language": "en",
                "token_kind": "word",
            }
        ],
        [
            {
                "unit_index": 0,
                "text": "x",
                "start_ms": 0,
                "end_ms": 20,
                "confidence": float("nan"),
                "language": "en",
                "token_kind": "token",
            }
        ],
        [
            {
                "unit_index": 0,
                "text": "x",
                "start_ms": 0,
                "end_ms": 20,
                "confidence": 1.1,
                "language": "en",
                "token_kind": "word",
            }
        ],
    ],
)
def test_sidecar_client_rejects_timed_unit_capability_mismatches(tmp_path, timed_units) -> None:
    class Transport:
        def get(self, url, **_kwargs):
            if url.endswith("/v1/capabilities"):
                return _sidecar_capabilities_response(
                    timed_units={
                        "unit_kinds": ["word"],
                        "time_base": "chunk_ms",
                        "precision_ms": 20,
                    }
                )
            return _sidecar_ready_response()

        def post(self, *_args, **_kwargs):
            result = {"kind": "speech", "segments": [{"start": 0, "end": 1, "text": "x"}]}
            if timed_units is not None:
                result["timed_units"] = timed_units
            return SidecarHttpResponse(status=200, body=json.dumps(_mod_response(result)).encode())

    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")
    with pytest.raises(SidecarProviderError, match="contract_incompatible"):
        SidecarTranscriptionClient("http://mod-whisper-cpu:8081", "token", Transport()).transcribe(
            asset_id="asset:1",
            chunk=TranscriptChunk(0.0, 1.0, "speaker:1", 0),
            audio_path=audio,
            idempotency_key="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="123e4567-e89b-42d3-a456-426614174002",
            prompt=None,
        )


def _timed_unit(**overrides: object) -> dict[str, object]:
    return {
        "unit_index": 0,
        "text": "x",
        "start_ms": 0,
        "end_ms": 20,
        "confidence": None,
        "language": "en",
        "token_kind": "word",
    } | overrides


@pytest.mark.parametrize(
    ("name", "timed_units"),
    [
        ("noninteger", [_timed_unit(start_ms=0.5)]),
        ("nonfinite", [_timed_unit(start_ms=float("nan"))]),
        ("range", [_timed_unit(end_ms=1020)]),
        ("index", [_timed_unit(unit_index=1)]),
        (
            "order",
            [
                _timed_unit(unit_index=0, start_ms=100, end_ms=120),
                _timed_unit(unit_index=1, start_ms=0, end_ms=20),
            ],
        ),
        ("grid", [_timed_unit(start_ms=10, end_ms=20)]),
        ("undeclared_kind", [_timed_unit(token_kind="token")]),
    ],
)
def test_transcriber_rejects_invalid_timed_units_before_completion_callback(
    tmp_path, name, timed_units
) -> None:
    completed: list[object] = []
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")

    class Transport:
        def get(self, url, **_kwargs):
            if url.endswith("/v1/capabilities"):
                return _sidecar_capabilities_response(
                    timed_units={
                        "unit_kinds": ["word"],
                        "time_base": "chunk_ms",
                        "precision_ms": 20,
                    }
                )
            return _sidecar_ready_response()

        def post(self, *_args, **_kwargs):
            return SidecarHttpResponse(
                status=200,
                body=json.dumps(
                    _mod_response(
                        {
                            "kind": "speech",
                            "segments": [{"start": 0, "end": 1, "text": "x"}],
                            "timed_units": timed_units,
                        }
                    )
                ).encode(),
            )

    class Lifecycle:
        def sent(self):
            return True

        def accepted(self, *_args):
            raise AssertionError(f"{name} response must not be accepted")

        def failed(self, error):
            assert isinstance(error, SidecarProviderError)
            assert error.category == "contract_incompatible"
            return True

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=SidecarTranscriptionClient(
            "http://mod-whisper-cpu:8081", "token", Transport()
        ),
    )
    with pytest.raises(SidecarProviderError) as error:
        transcriber.transcribe_chunks(
            tmp_path / "input.wav",
            [TranscriptChunk(0.0, 1.0, "speaker:1", 0)],
            tmp_path,
            asset_id="asset:1",
            sidecar_prepared_requests={
                0: SidecarPreparedRequest(
                    audio,
                    "a" * 64,
                    SidecarRequestIdentity(
                        "123e4567-e89b-42d3-a456-426614174001",
                        "123e4567-e89b-42d3-a456-426614174002",
                    ),
                    lifecycle=Lifecycle(),
                    on_completed=lambda *_args: completed.append("completed"),
                )
            },
        )
    assert error.value.category == "contract_incompatible"
    assert completed == []


def test_canonical_sidecar_request_hash_is_deterministic_before_client_io(tmp_path) -> None:
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF canonical PCM")
    chunk = TranscriptChunk(0.0, 1.0, "speaker:1", 0)
    kwargs = {
        "asset_id": "asset:1",
        "chunk": chunk,
        "idempotency_key": "123e4567-e89b-42d3-a456-426614174001",
        "prompt": "prompt",
        "audio_path": audio,
    }

    first = canonical_sidecar_request_hash(**kwargs)
    assert first == canonical_sidecar_request_hash(**kwargs)
    audio.write_bytes(b"RIFF changed PCM")
    assert first != canonical_sidecar_request_hash(**kwargs)


def test_prepared_sidecar_request_transitions_before_and_after_client_io(tmp_path) -> None:
    calls: list[str] = []
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")

    class Lifecycle:
        def sent(self):
            calls.append("sent")
            return True

        def accepted(self):
            calls.append("accepted")
            return True

        def completed(self):
            calls.append("completed")
            return True

        def failed(self, _error):
            calls.append("failed")
            return True

    class Sidecar:
        def transcribe(self, **_kwargs):
            calls.append("io")
            return "text"

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=Sidecar(),
    )
    request = SidecarPreparedRequest(
        audio_path=audio,
        request_hash="a" * 64,
        identity=SidecarRequestIdentity(
            idempotency_key="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="123e4567-e89b-42d3-a456-426614174002",
        ),
        lifecycle=Lifecycle(),
    )

    result = transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(0.0, 1.0, "speaker:1", 0)],
        tmp_path,
        asset_id="asset:1",
        sidecar_prepared_requests={0: request},
    )

    assert result[0].text == "text"
    assert calls == ["sent", "io", "accepted", "completed"]


def test_sidecar_client_sends_authenticated_cancellation_request(tmp_path) -> None:
    calls: list[dict[str, object]] = []

    class Transport:
        def post(self, url, *, headers, body, **_kwargs):
            calls.append({"url": url, "headers": headers, "body": body})
            return SidecarHttpResponse(status=204, body=b"")

    client = SidecarTranscriptionClient("http://mod-whisper-cpu:8081", "token", Transport())
    assert client.cancel("123e4567-e89b-42d3-a456-426614174001") == 204
    assert calls == [
        {
            "url": "http://mod-whisper-cpu:8081/v1/cancellations/123e4567-e89b-42d3-a456-426614174001",
            "headers": {"Authorization": "Bearer token", "Content-Length": "0"},
            "body": b"",
        }
    ]


def test_sidecar_startup_validation_requires_matching_ready_capabilities() -> None:
    model = {"id": "model:1", "revision": "r1", "sha256": "a" * 64}
    mod = {
        "id": "mod-whisper-cpu",
        "version": "1.0.0",
        "image_digest": "sha256:" + "b" * 64,
        "runtime": "whisper.cpp",
        "model": {**model, "license_ref": "MIT", "access_declaration": "public"},
    }

    class Transport:
        def get(self, url, *, headers, **_kwargs):
            assert headers == {"Authorization": "Bearer token"}
            if url.endswith("/v1/capabilities"):
                body = {
                    "contract_version": "v1",
                    "correlation_id": "123e4567-e89b-42d3-a456-426614174001",
                    "mod": mod,
                    "result": {
                        "offerings": [{"model_id": "model:1", "device_id": "cpu"}],
                        "max_audio_bytes": 25 * 1024 * 1024,
                        "max_audio_seconds": 120,
                        "readiness": "ready",
                    },
                }
            else:
                body = {"status": "ready", "model": model, "rss_mb": 1}
            return SidecarHttpResponse(status=200, body=json.dumps(body).encode())

    SidecarTranscriptionClient(
        "http://mod-whisper-cpu:8081", "token", Transport()
    ).validate_startup(expected_id="mod-whisper-cpu", expected_digest="sha256:" + "b" * 64)


def test_sidecar_startup_validation_requires_the_selected_cpu_offering() -> None:
    class Transport:
        def get(self, url, **_kwargs):
            if url.endswith("/v1/capabilities"):
                payload = _sidecar_capabilities_response().body
                body = json.loads(payload)
                body["result"]["offerings"] = [{"model_id": "ggml-base.en.bin", "device_id": "gpu"}]
                return SidecarHttpResponse(status=200, body=json.dumps(body).encode())
            raise AssertionError("readiness must not be queried for an incompatible offering")

    with pytest.raises(SidecarProviderError, match="selected CPU model"):
        SidecarTranscriptionClient(
            "http://mod-whisper-cpu:8081", "token", Transport()
        ).validate_startup(expected_id="mod-whisper-cpu", expected_digest="sha256:" + "a" * 64)


def test_prepared_sidecar_retry_replaces_the_invocation_identity(tmp_path) -> None:
    calls: list[object] = []
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")

    class Lifecycle:
        def sent(self):
            calls.append("sent")
            return True

        def accepted(self):
            calls.append("accepted")
            return True

        def retry(self, _error):
            calls.append("retry")
            return SidecarRequestIdentity(
                idempotency_key="123e4567-e89b-42d3-a456-426614174001",
                correlation_id="123e4567-e89b-42d3-a456-426614174003",
            )

        def completed(self):
            calls.append("completed")
            return True

        def failed(self, _error):
            calls.append("failed")
            return True

    class Sidecar:
        def __init__(self):
            self.attempts = 0

        def transcribe(self, **kwargs):
            self.attempts += 1
            calls.append(kwargs["correlation_id"])
            if self.attempts == 1:
                raise SidecarProviderError("unavailable", "down", retryable=True)
            return "text"

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=Sidecar(),
        sleeper=lambda _seconds: None,
    )
    request = SidecarPreparedRequest(
        audio_path=audio,
        request_hash="a" * 64,
        identity=SidecarRequestIdentity(
            idempotency_key="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="123e4567-e89b-42d3-a456-426614174002",
        ),
        lifecycle=Lifecycle(),
    )

    transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(0.0, 1.0, "speaker:1", 0)],
        tmp_path,
        asset_id="asset:1",
        sidecar_prepared_requests={0: request},
    )

    assert calls == [
        "sent",
        "123e4567-e89b-42d3-a456-426614174002",
        "retry",
        "sent",
        "123e4567-e89b-42d3-a456-426614174003",
        "accepted",
        "completed",
    ]


def _mod_response(result: dict[str, object]) -> dict[str, object]:
    return {
        "contract_version": "v1",
        "correlation_id": "123e4567-e89b-42d3-a456-426614174000",
        "mod": {
            "id": "mod-whisper-cpu",
            "version": "1.0.0",
            "image_digest": "sha256:" + "a" * 64,
            "runtime": "whisper.cpp",
            "model": {
                "id": "ggml-base.en.bin",
                "revision": "v1",
                "sha256": "a" * 64,
                "license_ref": "MIT",
                "access_declaration": "public",
            },
        },
        "result": result,
    }


def _mod_error_response(category: str, retryable: bool) -> dict[str, object]:
    response = _mod_response({"kind": "no_speech", "segments": []})
    response.pop("result")
    response["error"] = {
        "category": category,
        "message": "provider error",
        "retryable": retryable,
    }
    return response


def _sidecar_capabilities_response(
    *, readiness: str = "ready", timed_units: dict[str, object] | None = None
) -> SidecarHttpResponse:
    return SidecarHttpResponse(
        status=200,
        body=json.dumps(
            _mod_response(
                {
                    "offerings": [{"model_id": "ggml-base.en.bin", "device_id": "cpu"}],
                    "max_audio_bytes": 25 * 1024 * 1024,
                    "max_audio_seconds": 120,
                    "readiness": readiness,
                    **({"transcription": {"timed_units": timed_units}} if timed_units else {}),
                }
            )
        ).encode(),
    )


def _sidecar_ready_response() -> SidecarHttpResponse:
    return SidecarHttpResponse(
        status=200,
        body=json.dumps(
            {
                "status": "ready",
                "model": {
                    "id": "ggml-base.en.bin",
                    "revision": "v1",
                    "sha256": "a" * 64,
                },
                "rss_mb": 1,
            }
        ).encode(),
    )


def test_sidecar_client_rejects_post_startup_incompatible_readiness_without_post(tmp_path) -> None:
    calls: list[str] = []

    class Transport:
        def __init__(self) -> None:
            self.preflight_count = 0

        def get(self, url, **_kwargs):
            calls.append(url)
            self.preflight_count += 1
            if url.endswith("/v1/capabilities"):
                return _sidecar_capabilities_response()
            if self.preflight_count == 2:
                return _sidecar_ready_response()
            return SidecarHttpResponse(
                status=200,
                body=json.dumps(
                    {
                        "status": "ready",
                        "model": {"id": "other", "revision": "v1", "sha256": "b" * 64},
                        "rss_mb": 1,
                    }
                ).encode(),
            )

        def post(self, url, **_kwargs):
            calls.append(url)
            raise AssertionError("an incompatible sidecar must not receive audio")

    client = SidecarTranscriptionClient("http://mod-whisper-cpu:8081", "token", Transport())
    client.validate_startup(expected_id="mod-whisper-cpu", expected_digest="sha256:" + "a" * 64)
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")

    with pytest.raises(SidecarProviderError, match="contract_incompatible") as error:
        client.transcribe(
            asset_id="asset:1",
            chunk=TranscriptChunk(0.0, 1.0, "speaker:1", 0),
            audio_path=audio,
            idempotency_key="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="123e4567-e89b-42d3-a456-426614174002",
            prompt=None,
        )

    assert error.value.retryable is False
    assert calls == [
        "http://mod-whisper-cpu:8081/v1/capabilities",
        "http://mod-whisper-cpu:8081/readyz",
        "http://mod-whisper-cpu:8081/v1/capabilities",
        "http://mod-whisper-cpu:8081/readyz",
    ]


def test_sidecar_client_validates_authenticated_readiness_and_capabilities_before_post(
    tmp_path,
) -> None:
    calls: list[tuple[str, dict[str, str] | bytes]] = []

    class Transport:
        def get(self, url, *, headers, **_kwargs):
            calls.append((url, headers))
            if url.endswith("/v1/capabilities"):
                return _sidecar_capabilities_response()
            return _sidecar_ready_response()

        def post(self, url, *, headers, body, **_kwargs):
            calls.append((url, body))
            return SidecarHttpResponse(
                status=200,
                body=json.dumps(_mod_response({"kind": "no_speech", "segments": []})).encode(),
            )

    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")
    result = SidecarTranscriptionClient(
        "http://mod-whisper-cpu:8081", "token", Transport()
    ).transcribe(
        asset_id="asset:1",
        chunk=TranscriptChunk(0.0, 1.0, "speaker:1", 0),
        audio_path=audio,
        idempotency_key="123e4567-e89b-42d3-a456-426614174001",
        correlation_id="123e4567-e89b-42d3-a456-426614174002",
        prompt=None,
    )

    assert result.text == ""
    assert [url for url, _value in calls] == [
        "http://mod-whisper-cpu:8081/v1/capabilities",
        "http://mod-whisper-cpu:8081/readyz",
        "http://mod-whisper-cpu:8081/v1/transcriptions",
    ]
    assert calls[0][1] == {"Authorization": "Bearer token"}
    assert calls[1][1] == {"Authorization": "Bearer token"}


def test_sidecar_client_shares_one_deadline_across_preflight_and_post(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeouts: list[tuple[str, float, float, float]] = []
    now = [1000.0]

    class Transport:
        def get(self, url, *, connect_timeout, response_timeout, total_timeout, **_kwargs):
            timeouts.append((url, connect_timeout, response_timeout, total_timeout))
            now[0] += 3.0 if url.endswith("/v1/capabilities") else 4.0
            if url.endswith("/v1/capabilities"):
                return _sidecar_capabilities_response()
            return _sidecar_ready_response()

        def post(self, url, *, connect_timeout, response_timeout, total_timeout, **_kwargs):
            timeouts.append((url, connect_timeout, response_timeout, total_timeout))
            return SidecarHttpResponse(
                status=200,
                body=json.dumps(_mod_response({"kind": "no_speech", "segments": []})).encode(),
            )

    monkeypatch.setattr("stt_vault.processing.transcription.time.monotonic", lambda: now[0])
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")

    SidecarTranscriptionClient("http://mod-whisper-cpu:8081", "token", Transport()).transcribe(
        asset_id="asset:1",
        chunk=TranscriptChunk(0.0, 1.0, "speaker:1", 0),
        audio_path=audio,
        idempotency_key="123e4567-e89b-42d3-a456-426614174001",
        correlation_id="123e4567-e89b-42d3-a456-426614174002",
        prompt=None,
    )

    assert timeouts == [
        ("http://mod-whisper-cpu:8081/v1/capabilities", 2.0, 90.0, 95.0),
        ("http://mod-whisper-cpu:8081/readyz", 2.0, 90.0, 92.0),
        ("http://mod-whisper-cpu:8081/v1/transcriptions", 2.0, 88.0, 88.0),
    ]


def test_sidecar_client_stops_preflight_when_the_attempt_deadline_expires(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    now = [1000.0]

    class Transport:
        def get(self, url, **_kwargs):
            calls.append(url)
            now[0] += 95.0
            return _sidecar_capabilities_response()

        def post(self, url, **_kwargs):
            calls.append(url)
            raise AssertionError("an expired attempt must not send audio")

    monkeypatch.setattr("stt_vault.processing.transcription.time.monotonic", lambda: now[0])
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")

    with pytest.raises(SidecarProviderError, match="attempt deadline exceeded") as error:
        SidecarTranscriptionClient("http://mod-whisper-cpu:8081", "token", Transport()).transcribe(
            asset_id="asset:1",
            chunk=TranscriptChunk(0.0, 1.0, "speaker:1", 0),
            audio_path=audio,
            idempotency_key="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="123e4567-e89b-42d3-a456-426614174002",
            prompt=None,
        )

    assert error.value.retryable is True
    assert calls == ["http://mod-whisper-cpu:8081/v1/capabilities"]


def test_sidecar_retry_starts_a_fresh_deadline_for_each_attempt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    total_timeouts: list[float] = []
    now = [1000.0]
    post_attempts = 0

    class Transport:
        def get(self, url, *, total_timeout, **_kwargs):
            total_timeouts.append(total_timeout)
            now[0] += 2.0 if url.endswith("/v1/capabilities") else 3.0
            if url.endswith("/v1/capabilities"):
                return _sidecar_capabilities_response()
            return _sidecar_ready_response()

        def post(self, _url, *, total_timeout, **_kwargs):
            nonlocal post_attempts
            total_timeouts.append(total_timeout)
            post_attempts += 1
            if post_attempts == 1:
                raise SidecarProviderError("unavailable", "down", retryable=True)
            return SidecarHttpResponse(
                status=200,
                body=json.dumps(_mod_response({"kind": "no_speech", "segments": []})).encode(),
            )

    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"RIFF audio")
        return output_path

    monkeypatch.setattr("stt_vault.processing.transcription.time.monotonic", lambda: now[0])
    result = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=SidecarTranscriptionClient(
            "http://mod-whisper-cpu:8081", "token", Transport()
        ),
        chunk_extractor=extract_chunk,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    ).transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(0.0, 1.0, "speaker:1", 0)],
        tmp_path,
        asset_id="asset:1",
    )

    assert result[0].attempts == 2
    assert total_timeouts == [95.0, 93.0, 90.0, 95.0, 93.0, 90.0]


def test_sidecar_client_rejects_unready_preflight_without_post(tmp_path) -> None:
    calls: list[str] = []

    class Transport:
        def get(self, url, **_kwargs):
            calls.append(url)
            return _sidecar_capabilities_response(readiness="loading")

        def post(self, url, **_kwargs):
            calls.append(url)
            raise AssertionError("a sidecar that is not ready must not receive audio")

    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")
    with pytest.raises(SidecarProviderError, match="not_ready") as error:
        SidecarTranscriptionClient("http://mod-whisper-cpu:8081", "token", Transport()).transcribe(
            asset_id="asset:1",
            chunk=TranscriptChunk(0.0, 1.0, "speaker:1", 0),
            audio_path=audio,
            idempotency_key="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="123e4567-e89b-42d3-a456-426614174002",
            prompt=None,
        )

    assert error.value.retryable is True
    assert calls == ["http://mod-whisper-cpu:8081/v1/capabilities"]


def test_sidecar_retry_rechecks_preflight_and_preserves_request_identity(tmp_path) -> None:
    calls: list[tuple[str, bytes | None]] = []
    readiness_checks = 0

    class Transport:
        def get(self, url, **_kwargs):
            nonlocal readiness_checks
            calls.append((url, None))
            if url.endswith("/v1/capabilities"):
                readiness_checks += 1
                return _sidecar_capabilities_response(
                    readiness="loading" if readiness_checks == 1 else "ready"
                )
            return _sidecar_ready_response()

        def post(self, url, *, body, **_kwargs):
            calls.append((url, body))
            return SidecarHttpResponse(
                status=200,
                body=json.dumps(_mod_response({"kind": "no_speech", "segments": []})).encode(),
            )

    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"RIFF audio")
        return output_path

    identity = SidecarRequestIdentity(
        idempotency_key="123e4567-e89b-42d3-a456-426614174001",
        correlation_id="123e4567-e89b-42d3-a456-426614174002",
    )
    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=SidecarTranscriptionClient(
            "http://mod-whisper-cpu:8081", "token", Transport()
        ),
        chunk_extractor=extract_chunk,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    result = transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(0.0, 1.0, "speaker:1", 0)],
        tmp_path,
        asset_id="asset:1",
        sidecar_request_identities={0: identity},
    )

    assert result[0].attempts == 2
    assert [url for url, _body in calls] == [
        "http://mod-whisper-cpu:8081/v1/capabilities",
        "http://mod-whisper-cpu:8081/v1/capabilities",
        "http://mod-whisper-cpu:8081/readyz",
        "http://mod-whisper-cpu:8081/v1/transcriptions",
    ]
    assert identity.idempotency_key.encode() in calls[-1][1]
    assert identity.correlation_id.encode() in calls[-1][1]


def test_sidecar_client_sends_authenticated_v1_multipart_request(tmp_path) -> None:
    calls: list[dict[str, object]] = []

    class Transport:
        def get(self, url, **_kwargs):
            if url.endswith("/v1/capabilities"):
                return _sidecar_capabilities_response()
            return _sidecar_ready_response()

        def post(self, url, *, headers, body, connect_timeout, response_timeout, total_timeout):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "body": body,
                    "connect_timeout": connect_timeout,
                    "response_timeout": response_timeout,
                    "total_timeout": total_timeout,
                }
            )
            return SidecarHttpResponse(
                status=200,
                body=json.dumps(
                    _mod_response(
                        {
                            "kind": "speech",
                            "segments": [
                                {"start": 0, "end": 0.5, "text": "spoken"},
                                {"start": 0.5, "end": 1, "text": "words"},
                            ],
                        }
                    )
                ).encode(),
            )

    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")
    client = SidecarTranscriptionClient("http://mod-whisper-cpu:8081", "token", Transport())

    result = client.transcribe(
        asset_id="asset:1",
        chunk=TranscriptChunk(2.0, 3.0, "speaker:1", 3),
        audio_path=audio,
        idempotency_key="123e4567-e89b-42d3-a456-426614174001",
        correlation_id="123e4567-e89b-42d3-a456-426614174002",
        prompt="prompt",
    )
    assert result.text == "spoken words"
    assert result.provider_metadata["mod_id"] == "mod-whisper-cpu"
    assert result.timing_ms >= 0

    assert calls[0]["url"] == "http://mod-whisper-cpu:8081/v1/transcriptions"
    assert calls[0]["connect_timeout"] == 2.0
    assert calls[0]["response_timeout"] == 90.0
    assert 0 < calls[0]["total_timeout"] <= 95.0
    headers = calls[0]["headers"]
    assert headers["Authorization"] == "Bearer token"
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    body = calls[0]["body"]
    assert b'name="request"' in body
    assert b"Content-Type: application/json" in body
    assert b'name="audio"; filename="chunk.wav"' in body
    assert b"Content-Type: audio/wav" in body


def test_sidecar_client_maps_no_speech_and_rejects_invalid_or_retryable_errors(tmp_path) -> None:
    class Transport:
        def __init__(self, response: SidecarHttpResponse) -> None:
            self.response = response

        def get(self, url, **_kwargs):
            if url.endswith("/v1/capabilities"):
                return _sidecar_capabilities_response()
            return _sidecar_ready_response()

        def post(self, *_args, **_kwargs):
            return self.response

    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF audio")
    client = SidecarTranscriptionClient(
        "http://mod-whisper-cpu:8081",
        "token",
        Transport(
            SidecarHttpResponse(
                status=200,
                body=json.dumps(_mod_response({"kind": "no_speech", "segments": []})).encode(),
            )
        ),
    )
    assert (
        client.transcribe(
            asset_id="asset:1",
            chunk=TranscriptChunk(0.0, 1.0, "speaker:1", 0),
            audio_path=audio,
            idempotency_key="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="123e4567-e89b-42d3-a456-426614174002",
            prompt=None,
        ).text
        == ""
    )

    invalid = SidecarTranscriptionClient(
        "http://mod-whisper-cpu:8081",
        "token",
        Transport(SidecarHttpResponse(status=200, body=b"not json")),
    )
    with pytest.raises(SidecarProviderError, match="contract_incompatible"):
        invalid.transcribe(
            asset_id="asset:1",
            chunk=TranscriptChunk(0.0, 1.0, "speaker:1", 0),
            audio_path=audio,
            idempotency_key="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="123e4567-e89b-42d3-a456-426614174002",
            prompt=None,
        )

    retryable = SidecarTranscriptionClient(
        "http://mod-whisper-cpu:8081",
        "token",
        Transport(
            SidecarHttpResponse(
                status=503,
                body=json.dumps(_mod_error_response("resource_exhausted", True)).encode(),
            )
        ),
    )
    with pytest.raises(SidecarProviderError, match="resource_exhausted") as error:
        retryable.transcribe(
            asset_id="asset:1",
            chunk=TranscriptChunk(0.0, 1.0, "speaker:1", 0),
            audio_path=audio,
            idempotency_key="123e4567-e89b-42d3-a456-426614174001",
            correlation_id="123e4567-e89b-42d3-a456-426614174002",
            prompt=None,
        )
    assert error.value.retryable is True


def test_transcriber_selects_the_sidecar_without_openai_fallback_and_cleans_chunk(tmp_path) -> None:
    extracted: list[Path] = []
    calls: list[dict[str, object]] = []

    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"RIFF audio")
        extracted.append(output_path)
        return output_path

    class Sidecar:
        def transcribe(self, **kwargs):
            calls.append(kwargs)
            return "first segment second segment"

    def openai_factory(**_kwargs):
        raise AssertionError("the sidecar selection must not create an OpenAI client")

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=Sidecar(),
        client_factory=openai_factory,
        chunk_extractor=extract_chunk,
    )

    result = transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(2.0, 3.0, "speaker:1", 4)],
        tmp_path,
        asset_id="asset:1",
    )

    assert len(result) == 1
    segment = result[0]
    assert segment.start == 2.0
    assert segment.end == 3.0
    assert segment.chunk_start == 2.0
    assert segment.chunk_end == 3.0
    assert segment.speaker == "speaker:1"
    assert segment.text == "first segment second segment"
    assert segment.attempts == 1
    assert calls[0]["asset_id"] == "asset:1"
    assert extracted[0].suffix == ".wav"
    assert not extracted[0].exists()


def test_transcriber_rejects_an_unknown_provider_without_fallback() -> None:
    with pytest.raises(ValueError, match="unsupported transcription provider"):
        Transcriber(
            api_key="unused",
            base_url="unused",
            model="unused",
            prompt="",
            concurrency=1,
            retry_seconds=1,
            max_retries=1,
            provider="unknown",
        )


def test_sidecar_failure_removes_the_extracted_wav(tmp_path) -> None:
    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"RIFF audio")
        return output_path

    class Sidecar:
        def transcribe(self, **_kwargs):
            raise SidecarProviderError("invalid_request", "bad request", retryable=False)

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=Sidecar(),
        chunk_extractor=extract_chunk,
    )

    with pytest.raises(SidecarProviderError, match="invalid_request"):
        transcriber.transcribe_chunks(
            tmp_path / "input.wav",
            [TranscriptChunk(0.0, 1.0, "speaker:1", 0)],
            tmp_path,
            asset_id="asset:1",
        )

    assert not (tmp_path / "chunk-000000.wav").exists()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SidecarProviderError("unavailable", "down", retryable=True), (3, [2, 10])),
        (SidecarProviderError("not_ready", "loading", retryable=True), (3, [10, 30])),
        (SidecarProviderError("resource_exhausted", "full", retryable=True), (3, [10, 30])),
        (SidecarProviderError("provider_failure", "retry", retryable=True), (2, [10])),
        (SidecarProviderError("invalid_request", "bad", retryable=False), (1, [])),
    ],
)
def test_sidecar_retry_policy_matches_the_contract(error, expected) -> None:
    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        client=object(),
    )

    assert transcriber._retry_policy(error) == expected


def test_sidecar_retries_three_times_even_when_openai_retries_are_lower(tmp_path) -> None:
    attempts = 0

    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"RIFF audio")
        return output_path

    class Sidecar:
        def transcribe(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise SidecarProviderError("unavailable", "down", retryable=True)
            return "recovered"

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=Sidecar(),
        chunk_extractor=extract_chunk,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )

    result = transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(0.0, 1.0, "speaker:1", 0)],
        tmp_path,
        asset_id="asset:1",
    )

    assert attempts == 3
    assert result[0].attempts == 3


def test_sidecar_uses_worker_supplied_identity_for_each_retry(tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"RIFF audio")
        return output_path

    class Sidecar:
        def transcribe(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise SidecarProviderError("unavailable", "down", retryable=True)
            return "recovered"

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        provider="mod-whisper-cpu",
        sidecar_client=Sidecar(),
        chunk_extractor=extract_chunk,
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )
    identity = SidecarRequestIdentity(
        idempotency_key="123e4567-e89b-42d3-a456-426614174001",
        correlation_id="123e4567-e89b-42d3-a456-426614174002",
    )

    result = transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(0.0, 1.0, "speaker:1", 4)],
        tmp_path,
        asset_id="asset:1",
        sidecar_request_identities={4: identity},
    )

    assert result[0].attempts == 2
    assert [call["idempotency_key"] for call in calls] == [identity.idempotency_key] * 2
    assert [call["correlation_id"] for call in calls] == [identity.correlation_id] * 2


def test_transcriber_defaults_to_normalized_audio_chunk_extractor() -> None:
    class FakeTranscriptions:
        def create(self, **_kwargs):
            return {"text": "unused"}

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": FakeTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        client=FakeClient(),
    )

    assert transcriber.chunk_extractor is extract_audio_chunk


def test_transcriber_extracts_normalized_audio_and_uploads_the_wav_chunk(tmp_path) -> None:
    extracted_paths = []
    uploaded_chunks = []

    def extract_chunk(media_path, output_path, start, end):
        extracted_paths.append((media_path, output_path, start, end))
        output_path.write_bytes(b"normalized audio")
        return output_path

    class FakeTranscriptions:
        def create(self, *, file, **_kwargs):
            uploaded_chunks.append((file.name, file.read()))
            return {"text": "spoken words"}

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": FakeTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        client=FakeClient(),
        chunk_extractor=extract_chunk,
    )
    normalized_path = tmp_path / "audio.16k.mono.wav"

    result = transcriber.transcribe_chunks(
        normalized_path,
        [TranscriptChunk(1.0, 2.5, "SPEAKER_01", 0)],
        tmp_path,
    )

    assert extracted_paths == [(normalized_path, tmp_path / "chunk-000000.wav", 1.0, 2.5)]
    assert uploaded_chunks == [(str(tmp_path / "chunk-000000.wav"), b"normalized audio")]
    assert result[0].text == "spoken words"


def test_transcriber_uses_injected_client_and_chunk_extractor(tmp_path) -> None:
    extracted_chunks: list[tuple[float, float]] = []

    def extract_chunk(_media_path, output_path, start, end):
        extracted_chunks.append((start, end))
        output_path.write_bytes(b"audio")
        return output_path

    class FakeTranscriptions:
        def create(self, **_kwargs):
            return {"text": "spoken words"}

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": FakeTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        client=FakeClient(),
        chunk_extractor=extract_chunk,
    )

    result = transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(0.0, 1.5, "SPEAKER_01", 0)],
        tmp_path,
    )

    assert extracted_chunks == [(0.0, 1.5)]
    assert result[0].text == "spoken words"


def test_transcriber_rejects_a_mapping_response_without_text(tmp_path) -> None:
    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"audio")
        return output_path

    class FakeTranscriptions:
        def create(self, **_kwargs):
            return {"unexpected": "response"}

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": FakeTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=1,
        max_retries=1,
        client=FakeClient(),
        chunk_extractor=extract_chunk,
    )

    try:
        transcriber.transcribe_chunks(
            tmp_path / "input.wav",
            [TranscriptChunk(0.0, 1.5, "SPEAKER_01", 0)],
            tmp_path,
        )
    except TypeError as error:
        assert str(error) == "transcription response mapping is missing text"
    else:
        raise AssertionError("expected invalid response to fail")


def test_build_chunks_preserves_exact_60_second_request_windows() -> None:
    chunks = build_chunks([SpeakerSegment(10.0, 145.0, "SPEAKER_01")], max_seconds=60)

    assert [(chunk.chunk_index, chunk.start, chunk.end, chunk.speaker) for chunk in chunks] == [
        (0, 10.0, 70.0, "SPEAKER_01"),
        (1, 70.0, 130.0, "SPEAKER_01"),
        (2, 130.0, 145.0, "SPEAKER_01"),
    ]


def test_transcription_plan_uses_raw_speaker_turns() -> None:
    chunks = build_transcription_plan(
        [SpeakerSegment(0.0, 20.0, "SPEAKER_01"), SpeakerSegment(20.0, 30.0, "SPEAKER_02")],
        max_seconds=60,
    )

    assert [(chunk.start, chunk.end, chunk.speaker) for chunk in chunks] == [
        (0.0, 20.0, "SPEAKER_01"),
        (20.0, 30.0, "SPEAKER_02"),
    ]


def test_transcript_chunks_require_the_current_request_plan() -> None:
    chunks = build_chunks([SpeakerSegment(0.0, 75.0, "SPEAKER_01")], max_seconds=60)

    assert transcript_chunks_match_plan(
        [TranscriptSegment(0.0, 60.0, "SPEAKER_01", "", 0, 0.0, 60.0)],
        chunks,
    )
    assert not transcript_chunks_match_plan(
        [TranscriptSegment(0.0, 75.0, "SPEAKER_01", "", 0, 0.0, 60.0)],
        chunks,
    )


def test_ai_text_keeps_each_transcription_request_separate() -> None:
    text = to_ai_text(
        [
            TranscriptSegment(0.0, 10.0, "SPEAKER_01", "First turn."),
            TranscriptSegment(10.0, 20.0, "SPEAKER_01", "Second turn."),
        ]
    )

    assert text == (
        "[00:00:00.000] SPEAKER_01:\nFirst turn.\n\n[00:00:10.000] SPEAKER_01:\nSecond turn.\n"
    )


def test_transcriber_uses_injected_clock_and_sleeper_for_retries(tmp_path) -> None:
    sleeps: list[float] = []

    def extract_chunk(_media_path, output_path, _start, _end):
        output_path.write_bytes(b"audio")
        return output_path

    class FailingTranscriptions:
        def create(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": FailingTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=3,
        max_retries=2,
        client=FakeClient(),
        chunk_extractor=extract_chunk,
        clock=lambda: 100.0,
        sleeper=sleeps.append,
    )

    try:
        transcriber.transcribe_chunks(
            tmp_path / "input.wav",
            [TranscriptChunk(0.0, 1.5, "SPEAKER_01", 0)],
            tmp_path,
        )
    except RuntimeError as error:
        assert str(error) == "provider unavailable"
    else:
        raise AssertionError("expected retry exhaustion")
    assert sleeps == [3.0, 3.0]


def test_transcriber_retries_a_failed_attempt_then_reports_the_successful_attempt(tmp_path) -> None:
    retries: list[tuple[int, int, str, int]] = []
    extraction_count = 0

    def extract_chunk(_media_path, output_path, _start, _end):
        nonlocal extraction_count
        extraction_count += 1
        output_path.write_bytes(b"audio")
        return output_path

    class EventuallySuccessfulTranscriptions:
        def create(self, **_kwargs):
            if extraction_count == 1:
                raise RuntimeError("provider unavailable")
            return {"text": "recovered"}

    class FakeClient:
        audio = type("Audio", (), {"transcriptions": EventuallySuccessfulTranscriptions()})()

    transcriber = Transcriber(
        api_key="unused",
        base_url="unused",
        model="unused",
        prompt="",
        concurrency=1,
        retry_seconds=3,
        max_retries=2,
        client=FakeClient(),
        chunk_extractor=extract_chunk,
        clock=lambda: 100.0,
        sleeper=lambda _seconds: None,
        on_chunk_retry=lambda index, attempt, error, retry_at: retries.append(
            (index, attempt, str(error), retry_at)
        ),
    )

    result = transcriber.transcribe_chunks(
        tmp_path / "input.wav",
        [TranscriptChunk(0.0, 1.5, "SPEAKER_01", 0)],
        tmp_path,
    )

    assert extraction_count == 2
    assert retries == [(0, 1, "provider unavailable", 103)]
    assert result[0].attempts == 2
    assert result[0].text == "recovered"

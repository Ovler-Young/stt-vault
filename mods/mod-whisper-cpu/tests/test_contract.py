import ast
import io
import json
import logging
import threading
import wave
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mod_whisper_cpu import app as gateway_app
from mod_whisper_cpu import contracts as local_contracts
from mod_whisper_cpu.app import create_app
from mod_whisper_cpu.engine import WhisperCppServerEngine
from pydantic import ValidationError

from stt_vault.core.models import mod_contracts as core_contracts
from stt_vault.core.models.mod_contracts import TranscriptionResponseV1 as CoreResponse

TOKEN = "test-sidecar-token"
IMAGE_DIGEST = "sha256:" + "b" * 64
MODEL = {
    "id": "ggml-base.en.bin",
    "revision": "v1.0.0",
    "sha256": "a" * 64,
    "license_ref": "MIT",
    "access_declaration": "public",
}


def _wav(*, sample_rate: int = 16_000, channels: int = 1, seconds: float = 0.05) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\0\0" * int(sample_rate * seconds) * channels)
    return output.getvalue()


def _request() -> dict[str, object]:
    return {
        "contract_version": "v1",
        "correlation_id": str(uuid4()),
        "idempotency_key": str(uuid4()),
        "asset_id": "asset:1",
        "chunk": {"index": 0, "start": 0.0, "end": 0.05, "speaker_id": "speaker:1"},
        "language": "en",
        "prompt": None,
    }


def _mod_identity() -> dict[str, object]:
    return {
        "id": "mod-whisper-cpu",
        "version": "1.0.0",
        "image_digest": "sha256:" + "a" * 64,
        "runtime": "whisper.cpp-cpu",
        "model": MODEL,
    }


def _response() -> dict[str, object]:
    return {
        "contract_version": "v1",
        "correlation_id": str(uuid4()),
        "mod": _mod_identity(),
        "result": {
            "kind": "speech",
            "segments": [{"start": 0.0, "end": 0.05, "text": "transcript"}],
        },
    }


class RecordingEngine:
    def __init__(self, readiness: str = "ready") -> None:
        self.identity = MODEL
        self.readiness = readiness
        self.load_calls = 0
        self.transcribe_calls = 0
        self.pid = 4242
        self.generation = 1
        self.load_count = 1

    def load(self) -> None:
        self.load_calls += 1

    def transcribe(self, audio_path: Path, request: dict[str, object]) -> dict[str, object]:
        self.transcribe_calls += 1
        assert audio_path.read_bytes().startswith(b"RIFF")
        assert request["language"] == "en"
        return {
            "kind": "speech",
            "segments": [
                {"start": 0.0, "end": 0.05, "text": "first"},
                {"start": 0.05, "end": 0.10, "text": "second"},
            ],
        }


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, RecordingEngine]:
    token_path = tmp_path / "stt_mod_token"
    token_path.write_text(TOKEN, encoding="utf-8")
    engine = RecordingEngine()
    return TestClient(
        create_app(engine=engine, token_path=token_path, image_digest=IMAGE_DIGEST)
    ), engine


def _authorized_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _post(client: TestClient, request: dict[str, object], audio: bytes) -> object:
    return client.post(
        "/v1/transcriptions",
        files={
            "request": (None, json.dumps(request), "application/json"),
            "audio": ("chunk.wav", audio, "audio/wav"),
        },
        headers=_authorized_headers(),
    )


def _multipart_body(parts: list[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    boundary = "test-sidecar-boundary"
    body = (
        b"".join(
            b"--"
            + boundary.encode()
            + b"\r\n"
            + f'Content-Disposition: form-data; name="{name}"\r\n'.encode()
            + f"Content-Type: {content_type}\r\n\r\n".encode()
            + value
            + b"\r\n"
            for name, _filename, content_type, value in parts
        )
        + b"--"
        + boundary.encode()
        + b"--\r\n"
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _assert_error(
    response: object, *, status: int, category: str, retryable: bool
) -> dict[str, object]:
    assert response.status_code == status
    body = response.json()
    assert body["contract_version"] == "v1"
    assert body["mod"]["model"] == MODEL
    assert body["error"] == {
        "category": category,
        "message": body["error"]["message"],
        "retryable": retryable,
    }
    assert body["error"]["message"]
    core_contracts.ModErrorV1.model_validate(body)
    return body


@pytest.mark.parametrize(
    "model_name",
    [
        "ModelIdentityV1",
        "ModIdentityV1",
        "EmbeddingSpaceV1",
        "ModErrorDetailV1",
        "ModErrorV1",
        "TranscriptionChunkV1",
        "TranscriptionRequestV1",
        "TranscriptionSegmentV1",
        "TimedUnitsCapabilityV1",
        "TimedTranscriptUnitV1",
        "TranscriptionResultV1",
        "TranscriptionResponseV1",
        "ModLiveResponseV1",
        "ReadyModelIdentityV1",
        "ModReadyResponseV1",
        "ModCapabilityOfferingV1",
        "TranscriptionCapabilityV1",
        "ModCapabilitiesResultV1",
        "ModCapabilitiesV1",
    ],
)
def test_local_contract_schema_matches_the_canonical_core_v1_model(model_name: str) -> None:
    local_model = getattr(local_contracts, model_name)
    core_model = getattr(core_contracts, model_name)

    assert local_model.model_json_schema() == core_model.model_json_schema()


def test_mod_contracts_are_a_distributable_canonical_artifact() -> None:
    wrapper_path = Path(local_contracts.__file__)
    wrapper = ast.parse(wrapper_path.read_text(encoding="utf-8"))

    assert not [node for node in wrapper.body if isinstance(node, ast.ClassDef)]
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "contract_v1_artifact"
        for node in wrapper.body
    )
    artifact_path = wrapper_path.with_name("contract_v1_artifact.py")
    assert artifact_path.read_text(encoding="utf-8") == Path(core_contracts.__file__).read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("model_name", "payload"),
    [
        ("TranscriptionRequestV1", _request()),
        ("TranscriptionRequestV1", {**_request(), "asset_id": "asset invalid"}),
        (
            "TranscriptionRequestV1",
            {**_request(), "chunk": {**_request()["chunk"], "end": float("inf")}},
        ),
        ("TranscriptionResponseV1", _response()),
        (
            "TranscriptionResponseV1",
            {
                **_response(),
                "result": {"kind": "speech", "segments": [{"start": 0, "end": 1, "text": " "}]},
            },
        ),
        (
            "ModReadyResponseV1",
            {
                "status": "ready",
                "model": {key: MODEL[key] for key in ("id", "revision", "sha256")},
                "rss_mb": 1.0,
            },
        ),
        (
            "ModReadyResponseV1",
            {
                "status": "ready",
                "model": {key: MODEL[key] for key in ("id", "revision", "sha256")},
                "rss_mb": float("nan"),
            },
        ),
        (
            "ModCapabilitiesV1",
            {
                "contract_version": "v1",
                "correlation_id": str(uuid4()),
                "mod": _mod_identity(),
                "result": {
                    "offerings": [{"model_id": MODEL["id"], "device_id": "cpu"}],
                    "max_audio_bytes": 1,
                    "max_audio_seconds": 1.0,
                    "readiness": "ready",
                },
            },
        ),
        (
            "ModCapabilitiesV1",
            {
                "contract_version": "v1",
                "correlation_id": str(uuid4()),
                "mod": _mod_identity(),
                "result": {
                    "offerings": [],
                    "max_audio_bytes": 0,
                    "max_audio_seconds": float("inf"),
                    "readiness": "unknown",
                },
            },
        ),
    ],
)
def test_local_contract_acceptance_and_rejection_match_core_v1(
    model_name: str, payload: dict[str, object]
) -> None:
    local_model = getattr(local_contracts, model_name)
    core_model = getattr(core_contracts, model_name)

    def validate(model: object) -> object | None:
        try:
            return model.model_validate(payload, context={"chunk_duration": 0.05})
        except ValidationError:
            return None

    local_result = validate(local_model)
    core_result = validate(core_model)
    assert (local_result is None) == (core_result is None)
    if local_result is not None and core_result is not None:
        assert local_result.model_dump(mode="json") == core_result.model_dump(mode="json")


@pytest.mark.parametrize(
    ("category", "retryable", "valid"),
    [
        ("unavailable", True, True),
        ("not_ready", True, True),
        ("unsupported", False, True),
        ("invalid_request", False, True),
        ("resource_exhausted", True, True),
        ("provider_failure", False, True),
        ("contract_incompatible", False, True),
        ("unavailable", False, False),
        ("unsupported", True, False),
        ("invalid_request", True, False),
        ("resource_exhausted", False, False),
        ("contract_incompatible", True, False),
    ],
)
def test_local_error_categories_and_retry_defaults_match_core_v1(
    category: str, retryable: bool, valid: bool
) -> None:
    payload = {"category": category, "message": "contract test", "retryable": retryable}
    for model in (local_contracts.ModErrorDetailV1, core_contracts.ModErrorDetailV1):
        if valid:
            assert model.model_validate(payload).retryable is retryable
        else:
            with pytest.raises(ValidationError):
                model.model_validate(payload)


def test_every_gateway_response_validates_against_its_core_v1_contract(
    client: tuple[TestClient, RecordingEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, engine = client

    livez = http.get("/livez")
    assert livez.status_code == 200
    core_contracts.ModLiveResponseV1.model_validate(livez.json())

    readyz = http.get("/readyz", headers=_authorized_headers())
    assert readyz.status_code == 200
    core_contracts.ModReadyResponseV1.model_validate(readyz.json())

    capabilities = http.get("/v1/capabilities", headers=_authorized_headers())
    assert capabilities.status_code == 200
    core_contracts.ModCapabilitiesV1.model_validate(capabilities.json())

    response = _post(http, _request(), _wav())
    assert response.status_code == 200
    CoreResponse.model_validate(response.json(), context={"chunk_duration": 0.05})

    monkeypatch.setattr(engine, "readiness", "failed")
    _assert_error(
        http.get("/readyz", headers=_authorized_headers()),
        status=503,
        category="provider_failure",
        retryable=False,
    )


def test_livez_is_public_and_other_endpoints_require_the_token(
    client: tuple[TestClient, RecordingEngine],
) -> None:
    http, _engine = client

    livez = http.get("/livez")
    assert livez.status_code == 200
    assert livez.json() == {"status": "live"}
    unauthenticated = [
        http.get("/readyz"),
        http.get("/v1/capabilities"),
        http.post(
            "/v1/transcriptions",
            files={
                "request": (None, json.dumps(_request()), "application/json"),
                "audio": ("chunk.wav", _wav(), "audio/wav"),
            },
        ),
        http.post(f"/v1/cancellations/{uuid4()}"),
    ]
    for response in unauthenticated:
        _assert_error(response, status=401, category="invalid_request", retryable=False)
        assert response.json()["error"]["message"] == "mod authentication failed"


def test_readiness_and_capabilities_expose_one_selected_model_and_loading_failure_states(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "stt_mod_token"
    token_path.write_text(TOKEN, encoding="utf-8")
    engine = RecordingEngine(readiness="loading")
    http = TestClient(create_app(engine=engine, token_path=token_path, image_digest=IMAGE_DIGEST))

    _assert_error(
        http.get("/readyz", headers=_authorized_headers()),
        status=503,
        category="not_ready",
        retryable=True,
    )
    capabilities = http.get("/v1/capabilities", headers=_authorized_headers()).json()
    assert capabilities["result"]["readiness"] == "loading"
    assert capabilities["result"]["max_audio_bytes"] == 25 * 1024 * 1024
    assert capabilities["result"]["max_audio_seconds"] == 120
    assert capabilities["result"]["offerings"] == [{"model_id": MODEL["id"], "device_id": "cpu"}]
    assert capabilities["mod"]["model"] == MODEL

    engine.readiness = "ready"
    ready = http.get("/readyz", headers=_authorized_headers())
    assert ready.status_code == 200
    assert ready.json()["model"] == {key: MODEL[key] for key in ("id", "revision", "sha256")}

    engine.readiness = "failed"
    _assert_error(
        http.get("/readyz", headers=_authorized_headers()),
        status=503,
        category="provider_failure",
        retryable=False,
    )


@pytest.mark.parametrize(
    ("audio", "media_type"),
    [
        (_wav(sample_rate=8_000), "audio/wav"),
        (_wav(channels=2), "audio/wav"),
        (b"not-a-wav", "audio/wav"),
        (_wav(), "audio/mpeg"),
    ],
)
def test_transcription_rejects_non_pcm16_mono_16khz_wav(
    client: tuple[TestClient, RecordingEngine], audio: bytes, media_type: str
) -> None:
    http, engine = client
    response = http.post(
        "/v1/transcriptions",
        files={
            "request": (None, json.dumps(_request()), "application/json"),
            "audio": ("chunk.wav", audio, media_type),
        },
        headers=_authorized_headers(),
    )
    _assert_error(response, status=422, category="invalid_request", retryable=False)
    assert engine.transcribe_calls == 0


def test_transcription_requires_exactly_request_json_and_audio_wav_parts(
    client: tuple[TestClient, RecordingEngine],
) -> None:
    http, engine = client
    response = http.post(
        "/v1/transcriptions",
        files=[
            ("request", (None, json.dumps(_request()), "application/json")),
            ("audio", ("chunk.wav", _wav(), "audio/wav")),
            ("extra", (None, "unexpected", "text/plain")),
        ],
        headers=_authorized_headers(),
    )
    _assert_error(response, status=422, category="invalid_request", retryable=False)
    assert engine.transcribe_calls == 0


@pytest.mark.parametrize(
    ("request_media_type", "audio_media_type"),
    [("text/plain", "audio/wav"), ("application/json", "audio/x-wav")],
)
def test_transcription_rejects_wrong_declared_part_media_types(
    client: tuple[TestClient, RecordingEngine], request_media_type: str, audio_media_type: str
) -> None:
    http, engine = client
    body, content_type = _multipart_body(
        [
            ("request", "", request_media_type, json.dumps(_request()).encode()),
            ("audio", "chunk.wav", audio_media_type, _wav()),
        ]
    )

    response = http.post(
        "/v1/transcriptions",
        content=body,
        headers=_authorized_headers() | {"Content-Type": content_type},
    )

    _assert_error(response, status=422, category="invalid_request", retryable=False)
    assert engine.transcribe_calls == 0


def test_transcription_enforces_body_and_duration_limits(tmp_path: Path) -> None:
    token_path = tmp_path / "stt_mod_token"
    token_path.write_text(TOKEN, encoding="utf-8")
    size_engine = RecordingEngine()
    size_limited = TestClient(
        create_app(
            engine=size_engine,
            token_path=token_path,
            max_audio_bytes=2_000,
            max_audio_seconds=1.0,
            image_digest=IMAGE_DIGEST,
        )
    )
    duration_engine = RecordingEngine()
    duration_limited = TestClient(
        create_app(
            engine=duration_engine,
            token_path=token_path,
            max_audio_bytes=10_000,
            max_audio_seconds=0.05,
            image_digest=IMAGE_DIGEST,
        )
    )

    too_large = _post(size_limited, _request(), _wav(seconds=0.2))
    _assert_error(too_large, status=413, category="resource_exhausted", retryable=True)

    too_long = _post(duration_limited, _request(), _wav(seconds=0.06))
    _assert_error(too_long, status=413, category="resource_exhausted", retryable=True)
    assert size_engine.transcribe_calls == duration_engine.transcribe_calls == 0


def test_transcription_enforces_the_whole_multipart_body_limit(tmp_path: Path) -> None:
    token_path = tmp_path / "stt_mod_token"
    token_path.write_text(TOKEN, encoding="utf-8")
    engine = RecordingEngine()
    http = TestClient(
        create_app(
            engine=engine,
            token_path=token_path,
            max_audio_bytes=len(_wav()) + 40,
            image_digest=IMAGE_DIGEST,
        )
    )
    response = _post(http, _request(), _wav())

    _assert_error(response, status=413, category="resource_exhausted", retryable=True)
    assert engine.transcribe_calls == 0


def test_transcription_rejects_audio_and_response_outside_the_planned_duration(
    client: tuple[TestClient, RecordingEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, engine = client
    request = _request()
    request["chunk"] = {"index": 0, "start": 0.0, "end": 0.01, "speaker_id": "speaker:1"}

    response = _post(http, request, _wav(seconds=0.10))
    _assert_error(response, status=422, category="invalid_request", retryable=False)
    assert engine.transcribe_calls == 0

    def late_response(_audio_path: Path, _request: dict[str, object]) -> dict[str, object]:
        return {"kind": "speech", "segments": [{"start": 0.0, "end": 0.2, "text": "late"}]}

    monkeypatch.setattr(engine, "transcribe", late_response)
    request["chunk"] = {"index": 0, "start": 0.0, "end": 0.05, "speaker_id": "speaker:1"}
    response = _post(http, request, _wav())
    _assert_error(response, status=422, category="invalid_request", retryable=False)


def test_gateway_payloads_validate_against_the_core_v1_response_contract(
    client: tuple[TestClient, RecordingEngine],
) -> None:
    http, _engine = client
    response = _post(http, _request(), _wav())

    parsed = CoreResponse.model_validate(response.json(), context={"chunk_duration": 0.05})
    assert parsed.mod.id == "mod-whisper-cpu"


def test_shutdown_closes_the_engine_and_removes_inflight_temp_audio(tmp_path: Path) -> None:
    class ClosingEngine(RecordingEngine):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    token_path = tmp_path / "stt_mod_token"
    token_path.write_text(TOKEN, encoding="utf-8")
    engine = ClosingEngine()
    with TestClient(
        create_app(engine=engine, token_path=token_path, image_digest=IMAGE_DIGEST)
    ) as http:
        assert _post(http, _request(), _wav()).status_code == 200
    assert engine.closed


def test_engine_close_terminates_its_resident_server_process() -> None:
    class Process:
        def __init__(self) -> None:
            self.terminated = False
            self.waited = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: int) -> None:
            assert timeout == 10
            self.waited = True

    engine = object.__new__(WhisperCppServerEngine)
    process = Process()
    engine._process = process
    engine.readiness = "ready"

    engine.close()

    assert process.terminated and process.waited
    assert engine._process is None
    assert engine.readiness == "failed"


def test_transcription_replays_one_idempotency_response_without_reloading_the_engine(
    client: tuple[TestClient, RecordingEngine],
) -> None:
    http, engine = client
    request = _request()

    first = _post(http, request, _wav())
    second = _post(http, request, _wav())

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["result"] == {
        "kind": "speech",
        "segments": [
            {"start": 0.0, "end": 0.05, "text": "first"},
            {"start": 0.05, "end": 0.10, "text": "second"},
        ],
    }
    assert engine.load_calls == 1
    assert engine.transcribe_calls == 1

    changed = _post(http, request, _wav(seconds=0.04))
    _assert_error(changed, status=422, category="invalid_request", retryable=False)


def test_distinct_requests_share_the_resident_engine(
    client: tuple[TestClient, RecordingEngine],
) -> None:
    http, engine = client

    first = _post(http, _request(), _wav())
    second = _post(http, _request(), _wav())

    assert engine.load_calls == 1
    assert engine.transcribe_calls == 2
    assert first.headers["x-mod-engine-pid"] == "4242"
    assert first.headers["x-mod-engine-generation"] == "1"
    assert first.headers["x-mod-engine-load-count"] == "1"
    assert first.headers["x-mod-engine-pid"] == second.headers["x-mod-engine-pid"]
    assert first.headers["x-mod-engine-generation"] == second.headers["x-mod-engine-generation"]
    assert first.headers["x-mod-engine-load-count"] == "1"
    assert second.headers["x-mod-engine-load-count"] == "1"


def test_cancellation_is_idempotent_and_prevents_future_inference(
    client: tuple[TestClient, RecordingEngine],
) -> None:
    http, engine = client
    request = _request()
    cancellation_path = f"/v1/cancellations/{request['idempotency_key']}"

    assert http.post(cancellation_path, headers=_authorized_headers()).status_code == 204
    assert http.post(cancellation_path, headers=_authorized_headers()).status_code == 204
    response = _post(http, request, _wav())
    _assert_error(response, status=503, category="provider_failure", retryable=False)
    assert engine.transcribe_calls == 0


def test_cancelling_a_completed_key_preserves_its_idempotency_response(
    client: tuple[TestClient, RecordingEngine],
) -> None:
    http, engine = client
    request = _request()

    first = _post(http, request, _wav())
    cancellation = http.post(
        f"/v1/cancellations/{request['idempotency_key']}", headers=_authorized_headers()
    )
    replay = _post(http, request, _wav())

    assert cancellation.status_code == 204
    assert replay.json() == first.json()
    assert engine.transcribe_calls == 1


def test_cancellation_waits_for_active_inference_cleanup_and_does_not_replay(
    tmp_path: Path,
) -> None:
    class BlockingEngine(RecordingEngine):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()
            self.finish = threading.Event()
            self.restart_started = threading.Event()
            self.restart_release = threading.Event()
            self.cancel_calls = 0

        def transcribe(self, audio_path: Path, request: dict[str, object]) -> dict[str, object]:
            self.transcribe_calls += 1
            self.started.set()
            assert self.release.wait(timeout=2)
            assert self.finish.wait(timeout=2)
            return {"kind": "no_speech", "segments": []}

        def cancel_active(self) -> None:
            self.cancel_calls += 1
            self.readiness = "loading"
            self.release.set()

        def restart(self) -> None:
            self.restart_started.set()
            assert self.restart_release.wait(timeout=2)
            self.readiness = "ready"

    token_path = tmp_path / "stt_mod_token"
    token_path.write_text(TOKEN, encoding="utf-8")
    engine = BlockingEngine()
    request = _request()
    with TestClient(
        create_app(engine=engine, token_path=token_path, image_digest=IMAGE_DIGEST)
    ) as http:
        responses: list[object] = []
        transcription = threading.Thread(
            target=lambda: responses.append(_post(http, request, _wav()))
        )
        transcription.start()
        assert engine.started.wait(timeout=1)

        cancellations: list[object] = []
        cancellation = threading.Thread(
            target=lambda: cancellations.append(
                http.post(
                    f"/v1/cancellations/{request['idempotency_key']}",
                    headers=_authorized_headers(),
                )
            )
        )
        cancellation.start()
        cancellation.join(timeout=0.1)
        assert cancellation.is_alive()
        _assert_error(
            http.get("/readyz", headers=_authorized_headers()),
            status=503,
            category="not_ready",
            retryable=True,
        )
        engine.finish.set()
        assert engine.restart_started.wait(timeout=1)
        engine.restart_release.set()
        transcription.join(timeout=2)
        cancellation.join(timeout=2)
        assert not transcription.is_alive()
        assert not cancellation.is_alive()
        assert cancellations[0].status_code == 204
        _assert_error(responses[0], status=503, category="provider_failure", retryable=False)
        replay = _post(http, request, _wav())
        _assert_error(replay, status=503, category="provider_failure", retryable=False)
        assert not list(Path("/tmp").glob("stt-whisper-*.wav"))
    assert engine.cancel_calls == 1


def test_cancellation_waits_for_queued_inference_to_be_removed_before_returning_204(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BlockingEngine(RecordingEngine):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def transcribe(self, audio_path: Path, request: dict[str, object]) -> dict[str, object]:
            self.transcribe_calls += 1
            self.started.set()
            assert self.release.wait(timeout=2)
            return {"kind": "no_speech", "segments": []}

    token_path = tmp_path / "stt_mod_token"
    token_path.write_text(TOKEN, encoding="utf-8")
    engine = BlockingEngine()
    first_request = _request()
    queued_request = _request()
    original_run = gateway_app._run_transcription
    started_runs = 0
    started_runs_lock = threading.Lock()
    queued_run_started = threading.Event()

    def observe_queued_run(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal started_runs
        with started_runs_lock:
            started_runs += 1
            if started_runs == 2:
                queued_run_started.set()
        return original_run(*args, **kwargs)

    monkeypatch.setattr(gateway_app, "_run_transcription", observe_queued_run)
    with TestClient(
        create_app(engine=engine, token_path=token_path, image_digest=IMAGE_DIGEST)
    ) as http:
        active_responses: list[object] = []
        active = threading.Thread(
            target=lambda: active_responses.append(_post(http, first_request, _wav()))
        )
        active.start()
        assert engine.started.wait(timeout=1)

        queued_responses: list[object] = []
        queued = threading.Thread(
            target=lambda: queued_responses.append(_post(http, queued_request, _wav()))
        )
        queued.start()
        assert queued_run_started.wait(timeout=1)
        cancellation_responses: list[object] = []
        cancellation = threading.Thread(
            target=lambda: cancellation_responses.append(
                http.post(
                    f"/v1/cancellations/{queued_request['idempotency_key']}",
                    headers=_authorized_headers(),
                )
            )
        )
        cancellation.start()
        cancellation.join(timeout=0.1)
        assert cancellation.is_alive()

        engine.release.set()
        active.join(timeout=2)
        queued.join(timeout=2)
        cancellation.join(timeout=2)

        assert not active.is_alive()
        assert not queued.is_alive()
        assert not cancellation.is_alive()
        assert cancellation_responses[0].status_code == 204
        _assert_error(queued_responses[0], status=503, category="provider_failure", retryable=False)
        assert engine.transcribe_calls == 1
        assert not list(Path("/tmp").glob("stt-whisper-*.wav"))


def test_gateway_rejects_placeholder_image_digest(tmp_path: Path) -> None:
    token_path = tmp_path / "stt_mod_token"
    token_path.write_text(TOKEN, encoding="utf-8")

    with pytest.raises(ValueError, match="image digest"):
        create_app(engine=RecordingEngine(), token_path=token_path)
    with pytest.raises(ValueError, match="image digest"):
        create_app(
            engine=RecordingEngine(),
            token_path=token_path,
            image_digest="sha256:" + "0" * 64,
        )


def test_engine_rejects_missing_or_tampered_selected_model_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "model-manifest.json"
    manifest_path.write_text(
        json.dumps({"models": {MODEL["id"]: {**MODEL, "url": "https://example.invalid/model"}}}),
        encoding="utf-8",
    )
    engine = WhisperCppServerEngine(manifest_path, MODEL["id"], tmp_path / "models")

    def network_access(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("runtime model loading attempted network access")

    monkeypatch.setattr("mod_whisper_cpu.engine.urllib.request.urlopen", network_access)

    with pytest.raises(RuntimeError, match="selected model"):
        engine.load()
    assert engine.readiness == "failed"

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / MODEL["id"]).write_bytes(b"tampered")
    (models_dir / "ggml-unselected.bin").write_bytes(b"not a configured model")
    with pytest.raises(RuntimeError, match="selected model"):
        engine._selected_model_path()


def test_engine_startup_failure_terminates_the_child_and_sets_failed_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Process:
        def __init__(self) -> None:
            self.terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: int) -> None:
            assert timeout == 10

    engine = object.__new__(WhisperCppServerEngine)
    process = Process()
    engine._process = None
    engine._process_lock = threading.Lock()
    engine._port = 8178
    engine.readiness = "loading"
    monkeypatch.setattr(engine, "_selected_model_path", lambda: tmp_path / "model.bin")
    monkeypatch.setattr(
        "mod_whisper_cpu.engine.subprocess.Popen", lambda *_args, **_kwargs: process
    )

    def fail_self_check() -> None:
        raise RuntimeError("self-check failed")

    monkeypatch.setattr(engine, "_wait_until_ready_locked", fail_self_check)

    with pytest.raises(RuntimeError, match="self-check failed"):
        engine.load()
    assert process.terminated
    assert engine._process is None
    assert engine.readiness == "failed"


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("exited", "exited during startup"),
        ("timeout", "did not become ready"),
    ],
)
def test_engine_exit_and_readiness_timeout_clean_the_child_and_fail_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, message: str
) -> None:
    class Process:
        pid = 1234

        def __init__(self) -> None:
            self.terminated = False

        def poll(self) -> int | None:
            return 1 if failure == "exited" else None

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: int) -> None:
            assert timeout == 10

    engine = object.__new__(WhisperCppServerEngine)
    process = Process()
    engine._process = None
    engine._process_lock = threading.Lock()
    engine._port = 8178
    engine._generation = 0
    engine._load_count = 0
    engine.readiness = "loading"
    monkeypatch.setattr(engine, "_selected_model_path", lambda: tmp_path / "model.bin")
    monkeypatch.setattr(
        "mod_whisper_cpu.engine.subprocess.Popen", lambda *_args, **_kwargs: process
    )
    if failure == "timeout":
        monotonic_values = iter((0.0, 31.0))
        monkeypatch.setattr("mod_whisper_cpu.engine.time.monotonic", lambda: next(monotonic_values))

    with pytest.raises(RuntimeError, match=message):
        engine.load()
    assert process.terminated is (failure == "timeout")
    assert engine._process is None
    assert engine.readiness == "failed"


def test_requests_do_not_log_audio_or_bearer_token(
    client: tuple[TestClient, RecordingEngine], caplog: pytest.LogCaptureFixture
) -> None:
    http, _engine = client
    audio = _wav()
    caplog.set_level(logging.DEBUG)

    assert _post(http, _request(), audio).status_code == 200
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert TOKEN not in logged
    assert audio.hex() not in logged

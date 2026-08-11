import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.models.records import (
    AssetRecord,
    ClaimNextJob,
    FindProviderWorkItem,
    NewAsset,
    SpeakerSegment,
    TranscriptSegment,
)
from stt_vault.persistence.sqlite_database import SqliteDatabase
from stt_vault.processing.transcription import SidecarTranscriptionClient, Transcriber
from stt_vault.workers.worker_models import PreparedAsset, TranscriptionWork
from stt_vault.workers.worker_transcription import (
    ProviderJobContext,
    TranscriberConfig,
    TranscriptionStage,
)


def test_sidecar_stage_persists_real_http_timed_units_with_chunk_identity_and_offsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = [
        _speech_response(
            [
                _unit(0, "你", 0, 100, "word"),
                _unit(1, "好", 80, 200, "word"),
                _unit(2, "。", 200, 200, "punctuation"),
            ]
        ),
        _speech_response(
            [
                _unit(0, "repeat", 0, 100, "word"),
                _unit(1, "repeat", 100, 200, "word"),
                _unit(2, ",", 200, 200, "punctuation"),
            ]
        ),
    ]
    with _sidecar_server(responses) as base_url:
        database, stage, asset, prepared = _sidecar_stage(
            tmp_path, monkeypatch, base_url, database_name="timed-units.sqlite3"
        )

        segments, error = stage.transcribe(
            asset.id,
            asset,
            prepared,
            tmp_path,
            ProviderJobContext(asset.id, 1, 0),
        )

    assert error is None
    assert [(segment.speaker, segment.text) for segment in segments] == [
        ("SPEAKER_00", "你 好 。"),
        ("SPEAKER_01", "repeat repeat ,"),
    ]
    assert [
        (unit.chunk_index, unit.unit_index, unit.text, unit.start_ms, unit.end_ms, unit.token_kind)
        for unit in database.list_transcript_timed_units(asset.id)
    ] == [
        (0, 0, "你", 1235, 1335, "word"),
        (0, 1, "好", 1315, 1435, "word"),
        (0, 2, "。", 1435, 1435, "punctuation"),
        (1, 0, "repeat", 5679, 5779, "word"),
        (1, 1, "repeat", 5779, 5879, "word"),
        (1, 2, ",", 5879, 5879, "punctuation"),
    ]
    assert [
        (chunk.chunk_index, chunk.speaker, chunk.chunk_start, chunk.chunk_end)
        for chunk in database.list_transcript_chunks(asset.id)
    ] == [
        (0, "SPEAKER_00", 1.2345, 2.2345),
        (1, "SPEAKER_01", 5.6785, 6.6785),
    ]
    for index in (0, 1):
        invocation = database.find_provider_work_item(_find_transcription_work(asset.id, index))
        assert invocation is not None
        assert invocation.state == "completed"
        assert [
            transition.to_state
            for transition in database.list_provider_invocation_transitions(
                invocation.work_item_id, invocation.invocation_attempt
            )
        ] == ["prepared", "sent", "accepted", "completed"]


def test_sidecar_stage_retry_replaces_the_provider_invocation_without_duplicate_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = [
        _error_response("unavailable", True),
        _speech_response([_unit(0, "重试", 0, 200, "word")]),
        _speech_response([_unit(0, "后续", 0, 200, "word")]),
    ]
    with _sidecar_server(responses) as base_url:
        database, stage, asset, prepared = _sidecar_stage(
            tmp_path,
            monkeypatch,
            base_url,
            database_name="timed-retry.sqlite3",
            raw_segments=[SpeakerSegment(1.2345, 2.2345, "SPEAKER_00")],
        )

        segments, error = stage.transcribe(
            asset.id, asset, prepared, tmp_path, ProviderJobContext(asset.id, 1, 0)
        )

    assert error is None
    assert [segment.text for segment in segments] == ["重试"]
    assert [
        (unit.chunk_index, unit.unit_index, unit.text, unit.start_ms, unit.end_ms)
        for unit in database.list_transcript_timed_units(asset.id)
    ] == [(0, 0, "重试", 1235, 1435)]
    invocation = database.find_provider_work_item(_find_transcription_work(asset.id, 0))
    assert invocation is not None
    assert invocation.invocation_attempt == 2
    assert invocation.state == "completed"
    assert database.get_provider_invocation(invocation.work_item_id, 1).state == "failed"


def test_sidecar_stage_rolls_back_completion_without_changing_accepted_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _sidecar_server([_speech_response([_unit(0, "事务", 0, 200, "word")])]) as base_url:
        database, stage, asset, prepared = _sidecar_stage(
            tmp_path,
            monkeypatch,
            base_url,
            database_name="timed-rollback.sqlite3",
            raw_segments=[SpeakerSegment(1.2345, 2.2345, "SPEAKER_00")],
        )
        original_append_event = database._append_provider_audit_event

        def fail_completed_event(*args: object, **kwargs: object) -> None:
            if args[3] == "completed":
                raise RuntimeError("forced completion audit failure")
            original_append_event(*args, **kwargs)

        monkeypatch.setattr(database, "_append_provider_audit_event", fail_completed_event)
        segments, error = stage.transcribe(
            asset.id, asset, prepared, tmp_path, ProviderJobContext(asset.id, 1, 0)
        )

    assert segments == []
    assert isinstance(error, RuntimeError)
    invocation = database.find_provider_work_item(_find_transcription_work(asset.id, 0))
    assert invocation is not None
    assert invocation.state == "accepted"
    assert database.list_transcript_chunks(asset.id) == []
    assert database.list_transcript_timed_units(asset.id) == []
    assert [event.message for event in database.list_events(asset.id)] == [
        "transcribing speech",
        "Provider invocation prepared",
        "Provider invocation sent",
        "Provider invocation accepted",
    ]
    assert [
        transition.to_state
        for transition in database.list_provider_invocation_transitions(
            invocation.work_item_id, invocation.invocation_attempt
        )
    ] == ["prepared", "sent", "accepted"]


def _sidecar_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
    *,
    database_name: str,
    raw_segments: list[SpeakerSegment] | None = None,
) -> tuple[SqliteDatabase, TranscriptionStage, AssetRecord, PreparedAsset]:
    database = SqliteDatabase(tmp_path / database_name)
    database.initialize()
    input_path = tmp_path / "clip.wav"
    input_path.write_bytes(b"RIFF input")
    database.create_asset(NewAsset("asset-1", "clip.wav", "audio", input_path))
    assert database.claim_next_job(ClaimNextJob("worker-1", 30, now=10)) is not None
    asset = database.get_asset("asset-1")
    assert asset is not None
    raw_segments = raw_segments or [
        SpeakerSegment(1.2345, 2.2345, "SPEAKER_00"),
        SpeakerSegment(5.6785, 6.6785, "SPEAKER_01"),
    ]
    prepared = PreparedAsset(input_path, 7.0, {}, raw_segments, [], {})

    def extract_chunk(_input: Path, output: Path, _start: float, _end: float) -> Path:
        output.write_bytes(b"RIFF extracted")
        return output

    monkeypatch.setattr("stt_vault.workers.worker_transcription.extract_audio_chunk", extract_chunk)
    settings = SimpleNamespace(
        stt_transcription_provider="mod-whisper-cpu",
        openai_api_key="",
        openai_base_url="",
        openai_transcribe_model="ggml-base.en.bin",
        openai_transcribe_prompt="",
        openai_concurrency=1,
        openai_retry_seconds=1,
        openai_max_retries=1,
        parsed_openai_retry_backoff_seconds=[1],
        mod_whisper_cpu_image_digest="sha256:" + "a" * 64,
        transcribe_chunk_seconds=60.0,
        speaker_similarity_threshold=0.875,
    )
    client = SidecarTranscriptionClient(base_url, "test-token")

    def transcriber_factory(config: TranscriberConfig) -> Transcriber:
        return Transcriber(
            provider=config.provider,
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            prompt=config.prompt,
            concurrency=config.concurrency,
            retry_seconds=config.retry_seconds,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
            on_chunk_done=config.on_chunk_done,
            on_chunk_retry=config.on_chunk_retry,
            sidecar_client=client,
            clock=lambda: 0.0,
            sleeper=lambda _seconds: None,
        )

    return (
        database,
        TranscriptionStage(settings, database, transcriber_factory=transcriber_factory),
        asset,
        prepared,
    )


def _find_transcription_work(asset_id: str, index: int) -> FindProviderWorkItem:
    return FindProviderWorkItem(
        asset_id,
        asset_id,
        "transcription",
        "mod-whisper-cpu",
        "sha256:" + "a" * 64,
        f"chunk:{index}",
        0,
    )


def _unit(
    unit_index: int, text: str, start_ms: int, end_ms: int, token_kind: str
) -> dict[str, object]:
    return {
        "unit_index": unit_index,
        "text": text,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "confidence": None,
        "language": "zh",
        "token_kind": token_kind,
    }


def _speech_response(timed_units: list[dict[str, object]]) -> dict[str, object]:
    return _mod_response(
        {
            "kind": "speech",
            "segments": [
                {"start": 0, "end": 1, "text": " ".join(unit["text"] for unit in timed_units)}
            ],
            "timed_units": timed_units,
        }
    )


def _error_response(category: str, retryable: bool) -> dict[str, object]:
    response = _mod_response({"kind": "no_speech", "segments": []})
    response.pop("result")
    response["error"] = {
        "category": category,
        "message": "sidecar unavailable",
        "retryable": retryable,
    }
    return response


def _mod_response(result: dict[str, object]) -> dict[str, object]:
    return {
        "contract_version": "v1",
        "correlation_id": "123e4567-e89b-42d3-a456-426614174001",
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


@contextmanager
def _sidecar_server(responses: list[dict[str, object]]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            assert self.headers["Authorization"] == "Bearer test-token"
            if self.path == "/v1/capabilities":
                response = _mod_response(
                    {
                        "offerings": [{"model_id": "ggml-base.en.bin", "device_id": "cpu"}],
                        "max_audio_bytes": 25 * 1024 * 1024,
                        "max_audio_seconds": 120,
                        "readiness": "ready",
                        "transcription": {
                            "timed_units": {
                                "unit_kinds": ["word", "punctuation"],
                                "time_base": "chunk_ms",
                                "precision_ms": 20,
                            }
                        },
                    }
                )
            elif self.path == "/readyz":
                response = {
                    "status": "ready",
                    "model": {"id": "ggml-base.en.bin", "revision": "v1", "sha256": "a" * 64},
                    "rss_mb": 1,
                }
            else:
                self.send_error(404)
                return
            self._send_json(200, response)

        def do_POST(self) -> None:  # noqa: N802
            assert self.path == "/v1/transcriptions"
            assert self.headers["Authorization"] == "Bearer test-token"
            self.rfile.read(int(self.headers["Content-Length"]))
            response = responses.pop(0)
            self._send_json(503 if "error" in response else 200, response)

        def _send_json(self, status: int, response: dict[str, object]) -> None:
            body = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_transcription_stage_coordinates_storage_reconciliation_and_progress_events(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class FakeChunkPersistence:
        def prepare_work(self, _asset_id, _prepared):
            calls.append("prepare")
            return (
                TranscriptionWork(
                    chunks=[TranscriptSegment(0.0, 1.0, "SPEAKER_00", "", chunk_index=0)],
                    pending_chunks=[TranscriptSegment(0.0, 1.0, "SPEAKER_00", "", chunk_index=0)],
                    completed_chunks=0,
                ),
                False,
            )

        def save_success(self, _asset_id, _index, _result):
            calls.append("save")

        def recorded_segments(self, _asset_id):
            return []

    class FakeSpeakerReconciler:
        def reconcile(self, _prepared, segments):
            calls.append("reconcile")
            return segments

    class FakeProgressEvents:
        def start(self, _asset_id, _work, *, plan_changed):
            assert not plan_changed
            calls.append("start")

        def record_success(self, _asset_id, _work, _index):
            calls.append("progress")

        def record_retry(self, *_args):
            calls.append("retry")

    class FakeTranscriber:
        def __init__(self, config: TranscriberConfig):
            assert config.model == "model"
            self.on_chunk_done = config.on_chunk_done

        def transcribe_chunks(self, _media_path, chunks, _work_dir, **_kwargs):
            assert len(chunks) == 1
            result = TranscriptSegment(0.0, 1.0, "SPEAKER_00", "hello")
            self.on_chunk_done(0, result)
            return [result]

    settings = SimpleNamespace(
        stt_transcription_provider="openai",
        openai_api_key="",
        openai_base_url="",
        openai_transcribe_model="model",
        openai_transcribe_prompt="",
        openai_concurrency=1,
        openai_retry_seconds=1,
        openai_max_retries=1,
        parsed_openai_retry_backoff_seconds=[1],
    )
    stage = TranscriptionStage(
        settings,
        transcriber_factory=FakeTranscriber,
        chunk_persistence=FakeChunkPersistence(),
        speaker_reconciler=FakeSpeakerReconciler(),
        progress_events=FakeProgressEvents(),
        database=SimpleNamespace(),
    )

    segments, error = stage.transcribe(
        "asset-1",
        AssetRecord("asset-1", "clip.wav", "audio", str(tmp_path / "clip.wav"), "processing", 1, 1),
        PreparedAsset(tmp_path / "audio.wav", 1.0, {}, [], [], {}),
        tmp_path,
    )

    assert error is None
    assert segments == [TranscriptSegment(0.0, 1.0, "SPEAKER_00", "hello")]
    assert calls == ["prepare", "start", "reconcile", "save", "progress", "reconcile"]


@pytest.mark.parametrize("suffix", ["wav", "mp3", "m4a", "mp4"])
def test_transcription_stage_uses_normalized_diarization_audio(tmp_path: Path, suffix: str) -> None:
    media_paths: list[Path] = []

    class FakeChunkPersistence:
        def prepare_work(self, _asset_id, _prepared):
            return (
                TranscriptionWork(
                    chunks=[TranscriptSegment(1.0, 2.0, "SPEAKER_00", "", chunk_index=0)],
                    pending_chunks=[TranscriptSegment(1.0, 2.0, "SPEAKER_00", "", chunk_index=0)],
                    completed_chunks=0,
                ),
                False,
            )

        def save_success(self, _asset_id, _index, _result):
            return None

        def recorded_segments(self, _asset_id):
            return []

    class FakeSpeakerReconciler:
        def reconcile(self, _prepared, segments):
            return segments

    class FakeProgressEvents:
        def start(self, _asset_id, _work, *, plan_changed):
            return None

        def record_success(self, _asset_id, _work, _index):
            return None

        def record_retry(self, *_args):
            return None

    class FakeTranscriber:
        def __init__(self, _config: TranscriberConfig):
            return None

        def transcribe_chunks(self, media_path, chunks, _work_dir, **_kwargs):
            media_paths.append(media_path)
            return [TranscriptSegment(chunks[0].start, chunks[0].end, chunks[0].speaker, "hello")]

    settings = SimpleNamespace(
        stt_db_path=tmp_path / "app.sqlite3",
        stt_transcription_provider="openai",
        openai_api_key="",
        openai_base_url="",
        openai_transcribe_model="model",
        openai_transcribe_prompt="",
        openai_concurrency=1,
        openai_retry_seconds=1,
        openai_max_retries=1,
        parsed_openai_retry_backoff_seconds=[1],
    )
    normalized_path = tmp_path / "audio.16k.mono.wav"
    stage = TranscriptionStage(
        settings,
        transcriber_factory=FakeTranscriber,
        chunk_persistence=FakeChunkPersistence(),
        speaker_reconciler=FakeSpeakerReconciler(),
        progress_events=FakeProgressEvents(),
        database=SimpleNamespace(),
    )

    segments, error = stage.transcribe(
        "asset-1",
        AssetRecord(
            "asset-1",
            f"clip.{suffix}",
            "audio",
            str(tmp_path / f"clip.{suffix}"),
            "processing",
            1,
            1,
        ),
        PreparedAsset(normalized_path, 2.0, {}, [], [], {}),
        tmp_path,
    )

    assert error is None
    assert segments[0].text == "hello"
    assert media_paths == [normalized_path]

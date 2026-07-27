import json
import logging
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.logging_config import StructuredFormatter
from stt_vault.processing.diarization import DiarizerManager
from stt_vault.processing.summary_service import generate_asset_summary
from stt_vault.workers.worker import Worker
from stt_vault.workers.worker_completion import CompletionStage
from stt_vault.workers.worker_failure import WorkerFailureHandler
from stt_vault.workers.worker_models import PreparedAsset


def test_summary_generation_uses_injected_repository() -> None:
    calls: list[tuple[str, object]] = []

    class FakeRepository:
        def get_asset(self, asset_id: str):
            calls.append(("get", asset_id))
            return {
                "status": "success",
                "transcript_segments": [
                    {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "Hello"}
                ],
            }

        def update_asset_summary(self, asset_id: str, **kwargs):
            calls.append(("summary", (asset_id, kwargs)))

        def apply_ai_speaker_names(self, asset_id: str, speaker_names: dict[str, str]):
            calls.append(("speakers", (asset_id, speaker_names)))
            return speaker_names

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"title":"Hello","content_summary":"Greeting","highlights":[]}'
                            )
                        )
                    )
                ]
            )

    settings = SimpleNamespace(
        openai_speaker_name_confidence=0.9,
        openai_api_key="",
        openai_base_url="",
        openai_summary_model="model",
    )
    result = generate_asset_summary(
        settings,
        "asset-1",
        repository=FakeRepository(),
        client_factory=lambda **_kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        ),
    )

    assert result["title"] == "Hello"
    assert [name for name, _payload in calls] == ["get", "summary", "speakers", "summary"]


def test_complete_asset_persists_before_generating_summary(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    stage = CompletionStage(
        SimpleNamespace(stt_db_path=Path("app.sqlite3")),
        summary_generator=lambda _settings, asset_id: calls.append(("generate-summary", asset_id)),
    )

    monkeypatch.setattr(
        "stt_vault.workers.worker_completion.db.mark_success",
        lambda _db_path, asset_id, **kwargs: calls.append(("asset-success", (asset_id, kwargs))),
    )

    stage.complete(
        "asset-1",
        PreparedAsset(
            wav_path=Path("audio.wav"),
            duration=12.0,
            diarization_stats={},
            raw_segments=[],
            merged_segments=[],
            speaker_centroids={},
        ),
        transcript_segments=[{"text": "hello"}],
        exports={"srt": "asset.srt"},
    )

    assert [name for name, _payload in calls] == [
        "asset-success",
        "generate-summary",
    ]
    assert calls[0][1][1]["transcript_segments"] == [{"text": "hello"}]


def test_batched_diarization_logs_json_without_provider_console_output(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    wav_path = tmp_path / "private.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00")

    provider = SimpleNamespace(
        _validate_wav_file=lambda _wav_file, _path: None,
        _perform_vad=lambda _path: [],
    )
    manager = DiarizerManager(device="cpu", idle_timeout_seconds=1)

    with caplog.at_level(logging.INFO, logger="stt_vault.processing.diarization"):
        assert manager._diarize_batched(provider, str(wav_path)) is None

    record = caplog.records[-1]
    event = json.loads(StructuredFormatter().format(record))
    assert event["event_name"] == "diarization.started"
    assert event["media_filename"] == "private.wav"
    assert str(wav_path.parent) not in StructuredFormatter().format(record)


def test_worker_process_asset_orchestrates_stage_services(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    prepared = PreparedAsset(
        wav_path=tmp_path / "audio.wav",
        duration=12.0,
        diarization_stats={},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={},
    )
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(
        stt_db_path=tmp_path / "app.sqlite3", tmp_dir=tmp_path / "tmp"
    )
    worker.media_preparation = SimpleNamespace(
        prepare=lambda asset_id, asset: (
            calls.append("prepare"),
            (prepared.wav_path, prepared.duration),
        )[1]
    )
    worker.diarization = SimpleNamespace(diarize=lambda asset_id, wav_path, duration: prepared)
    worker.transcription = SimpleNamespace(
        transcribe=lambda asset_id, asset, stage_prepared, work_dir: (
            calls.append("transcribe"),
            ([{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "hello"}], None),
        )[1]
    )
    worker.transcript_exports = SimpleNamespace(
        write=lambda asset_id, asset, stage_prepared, segments, **kwargs: (
            calls.append(f"exports:{kwargs['partial']}"),
            {"json": "transcript.json"},
        )[1]
    )
    worker.visual_events = SimpleNamespace(detect=lambda _asset_id, _asset: {})
    worker.completion = SimpleNamespace(
        complete=lambda asset_id, stage_prepared, segments, exports: calls.append("complete")
    )
    worker.repository = SimpleNamespace(
        get_asset=lambda _asset_id: {
            "id": "asset-1",
            "filename": "clip.wav",
            "original_path": str(tmp_path / "clip.wav"),
        },
        list_transcript_chunks=lambda _asset_id: [],
    )

    worker.process_asset("asset-1")

    assert calls == ["prepare", "transcribe", "exports:False", "complete"]
    assert not (tmp_path / "tmp" / "asset-1").exists()


def test_worker_uses_named_injected_stage_factories() -> None:
    diarizer = SimpleNamespace()
    media_stage = SimpleNamespace()
    diarization_stage = SimpleNamespace()
    transcription_stage = SimpleNamespace()
    visual_stage = SimpleNamespace()
    completion_stage = SimpleNamespace()
    calls: list[str] = []

    worker = Worker(
        SimpleNamespace(),
        diarizer_factory=lambda _settings: (calls.append("diarizer"), diarizer)[1],
        media_preparation_stage_factory=lambda _settings: (
            calls.append("media"),
            media_stage,
        )[1],
        diarization_stage_factory=lambda _settings, value: (
            calls.append("diarization"),
            diarization_stage if value is diarizer else None,
        )[1],
        transcription_stage_factory=lambda _settings: (
            calls.append("transcription"),
            transcription_stage,
        )[1],
        visual_event_stage_factory=lambda _settings: (
            calls.append("visual"),
            visual_stage,
        )[1],
        transcript_export_stage_factory=lambda _settings: (
            calls.append("exports"),
            SimpleNamespace(),
        )[1],
        completion_stage_factory=lambda _settings: (
            calls.append("completion"),
            completion_stage,
        )[1],
        repository=SimpleNamespace(),
    )

    assert calls == [
        "diarizer",
        "media",
        "diarization",
        "visual",
        "transcription",
        "exports",
        "completion",
    ]
    assert worker.diarizer is diarizer
    assert worker.media_preparation is media_stage
    assert worker.diarization is diarization_stage
    assert worker.transcription is transcription_stage
    assert worker.completion is completion_stage


def test_worker_failure_handler_classifies_persisted_errors() -> None:
    assert WorkerFailureHandler._classify(OSError("/srv/private/clip.wav")) == {
        "category": "filesystem",
        "message": "A local processing operation failed",
    }
    assert WorkerFailureHandler._classify(RuntimeError("processing failed")) == {
        "category": "processing",
        "message": "Asset processing failed",
    }


def test_worker_failure_handler_persists_category_and_logs_safe_diagnostics(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.failures: list[tuple[str, dict[str, str]]] = []

        def mark_failed(self, asset_id: str, error: dict[str, str]) -> None:
            self.failures.append((asset_id, error))

    monkeypatch.setattr(
        "stt_vault.workers.worker_failure.job_log_context",
        lambda _db_path, asset_id: {
            "asset_id": asset_id,
            "job_id": "job-1",
            "details": {
                "authorization": "Bearer nested-secret",
                "levels": {"one": {"two": {"three": {"four": "hidden"}}}},
            },
        },
    )
    repository = FakeRepository()
    handler = WorkerFailureHandler(
        SimpleNamespace(stt_db_path=tmp_path / "app.sqlite3"), repository
    )

    with caplog.at_level(logging.ERROR, logger="stt_vault.workers.worker_failure"):
        handler.handle("asset-1", OSError("failed at /srv/private/clip.wav token=secret-value"))

    assert repository.failures == [
        (
            "asset-1",
            {"category": "filesystem", "message": "A local processing operation failed"},
        )
    ]
    events = [json.loads(StructuredFormatter().format(record)) for record in caplog.records]
    assert [event["event_name"] for event in events] == [
        "worker.job_failed",
        "worker.failure_categorized",
    ]
    for record, event in zip(caplog.records, events, strict=True):
        rendered = json.dumps(event)
        assert record.exc_info is None
        assert "/srv/private/clip.wav" not in rendered
        assert "secret-value" not in rendered
        assert "nested-secret" not in rendered
        assert event["cause"] == "failed at [path] [redacted]"
        assert event["details"]["authorization"] == "[redacted]"
        assert event["details"]["levels"]["one"]["two"]["three"] == "[truncated]"

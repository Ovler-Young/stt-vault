from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from stt_vault.core.api_models import DiarizationResult
from stt_vault.processing.diarization import DiarizerManager
from stt_vault.workers.worker import Worker
from stt_vault.workers.worker_completion import CompletionStage
from stt_vault.workers.worker_exports import VisualEventStage
from stt_vault.workers.worker_models import PreparedAsset, TranscriptionWork
from stt_vault.workers.worker_transcription import TranscriptionStage


def test_complete_asset_persists_before_generating_summary(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    stage = CompletionStage(
        SimpleNamespace(stt_db_path=Path("app.sqlite3")),
        summary_generator=lambda _settings, asset_id: calls.append(("generate-summary", asset_id)),
    )

    monkeypatch.setattr(
        "stt_vault.workers.worker.db.mark_success",
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
    monkeypatch.setattr(
        "stt_vault.workers.worker.db.get_asset",
        lambda _db_path, _asset_id: {
            "id": "asset-1",
            "filename": "clip.wav",
            "original_path": str(tmp_path / "clip.wav"),
        },
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker.db.list_transcript_chunks",
        lambda _db_path, _asset_id: [],
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


def test_diarization_model_rejects_malformed_provider_data() -> None:
    with pytest.raises(ValidationError):
        DiarizationResult.model_validate(
            {
                "raw_segments": [{"start": "bad", "end": 1.0, "speaker": "SPEAKER_00"}],
                "merged_segments": [],
                "speaker_centroids": {},
                "timing_stats": {},
            }
        )


def test_diarizer_manager_rejects_malformed_provider_result() -> None:
    class MalformedProvider:
        def diarize(self, _wav_path: str, *, generate_colors: bool) -> object:
            assert generate_colors
            return {
                "raw_segments": [{"start": "bad", "end": 1.0, "speaker": "SPEAKER_00"}],
                "merged_segments": [],
                "speaker_centroids": {},
                "timing_stats": {},
            }

    manager = DiarizerManager(device="cpu", idle_timeout_seconds=1)
    manager._diarizer = MalformedProvider()

    with pytest.raises(ValidationError):
        manager.diarize("audio.wav")


def test_visual_event_stage_persists_and_exports_video_events(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    stage = VisualEventStage(
        SimpleNamespace(
            stt_db_path=tmp_path / "app.sqlite3",
            exports_dir=tmp_path / "exports",
            visual_sample_interval_seconds=2.0,
            visual_change_threshold=18.0,
            visual_min_gap_seconds=6.0,
        )
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.db.update_stage", lambda *_args: calls.append("stage")
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.detect_slide_changes",
        lambda *_args, **_kwargs: [{"timestamp": 2.0, "score": 20.0, "kind": "slide_change"}],
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.db.replace_visual_events",
        lambda *_args: calls.append("persist"),
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.write_visual_event_thumbnails",
        lambda *_args, **_kwargs: calls.append("thumbnails"),
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.write_visual_events_export",
        lambda *_args: "events.json",
    )

    exports = stage.detect(
        "asset-1", {"media_type": "video", "original_path": str(tmp_path / "clip.mp4")}
    )

    assert calls == ["stage", "persist", "thumbnails"]
    assert exports == {"visual_events": "events.json"}


def test_visual_event_stage_propagates_injected_thumbnail_extractor(
    monkeypatch, tmp_path: Path
) -> None:
    def extractor(_media_path: Path, output_path: Path, _timestamp: float, _runner: object) -> Path:
        return output_path

    def runner(_command: list[str]) -> object:
        raise AssertionError("the injected extractor must receive the runner without invoking it")

    stage = VisualEventStage(
        SimpleNamespace(
            stt_db_path=tmp_path / "app.sqlite3",
            exports_dir=tmp_path / "exports",
            visual_sample_interval_seconds=2.0,
            visual_change_threshold=18.0,
            visual_min_gap_seconds=6.0,
        ),
        thumbnail_runner=runner,
        thumbnail_extractor=extractor,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr("stt_vault.workers.worker_exports.db.update_stage", lambda *_args: None)
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.detect_slide_changes",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.db.replace_visual_events", lambda *_args: None
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.write_visual_event_thumbnails",
        lambda *_args, **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.write_visual_events_export", lambda *_args: "events.json"
    )

    stage.detect("asset-1", {"media_type": "video", "original_path": str(tmp_path / "clip.mp4")})

    assert captured["extractor"] is extractor
    assert captured["runner"] is runner


def test_transcription_stage_coordinates_storage_reconciliation_and_progress_events(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class FakeChunkPersistence:
        def prepare_work(self, _asset_id, _prepared):
            calls.append("prepare")
            return (
                TranscriptionWork(
                    chunks=[{"chunk_index": 0, "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
                    pending_chunks=[
                        {"chunk_index": 0, "start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}
                    ],
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
        def __init__(self, **kwargs):
            self.on_chunk_done = kwargs["on_chunk_done"]

        def transcribe_chunks(self, _media_path, chunks, _work_dir):
            assert len(chunks) == 1
            result = {
                "start": 0.0,
                "end": 1.0,
                "speaker": "SPEAKER_00",
                "text": "hello",
            }
            self.on_chunk_done(0, result)
            return [result]

    settings = SimpleNamespace(
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
    )

    segments, error = stage.transcribe(
        "asset-1",
        {"original_path": str(tmp_path / "clip.wav")},
        PreparedAsset(tmp_path / "audio.wav", 1.0, {}, [], [], {}),
        tmp_path,
    )

    assert error is None
    assert segments == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "hello"}]
    assert calls == ["prepare", "start", "reconcile", "save", "progress", "reconcile"]

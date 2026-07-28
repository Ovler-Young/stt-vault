from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from stt_vault.core.models.api import DiarizationResult
from stt_vault.processing.diarization import DiarizerManager
from stt_vault.processing.diarization_contracts import ProviderDiarizationPayload
from stt_vault.workers.worker_exports import VisualEventStage
from stt_vault.workers.worker_models import PreparedAsset, TranscriptionWork
from stt_vault.workers.worker_transcription import (
    TranscriberConfig,
    TranscriptionStage,
    create_transcriber,
)


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
        def diarize(self, _wav_path: str, *, generate_colors: bool) -> ProviderDiarizationPayload:
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


def test_diarizer_manager_rejects_non_array_provider_centroid() -> None:
    class MalformedProvider:
        def diarize(self, _wav_path: str, *, generate_colors: bool) -> ProviderDiarizationPayload:
            assert generate_colors
            return {
                "raw_segments": [],
                "merged_segments": [],
                "speaker_centroids": {"SPEAKER_00": [0.1, 0.2]},
                "timing_stats": {},
            }

    manager = DiarizerManager(device="cpu", idle_timeout_seconds=1)
    manager._diarizer = MalformedProvider()

    with pytest.raises(ValueError, match="invalid speaker centroid"):
        manager.diarize("audio.wav")


def test_diarizer_manager_uses_injected_factory() -> None:
    calls: list[str] = []

    class EmptyProvider:
        def diarize(
            self, _wav_path: str, *, generate_colors: bool
        ) -> ProviderDiarizationPayload | None:
            assert generate_colors
            return None

    provider = EmptyProvider()
    manager = DiarizerManager(
        device="cpu",
        idle_timeout_seconds=1,
        diarizer_factory=lambda device: calls.append(device) or provider,
    )

    assert manager.diarize("audio.wav") is None
    assert calls == ["cpu"]
    assert manager._diarizer is provider


def test_create_transcriber_maps_every_config_field(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeTranscriber:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("stt_vault.workers.worker_transcription.Transcriber", FakeTranscriber)

    def on_chunk_done(_index, _result):
        return None

    def on_chunk_retry(_index, _attempt, _error, _retry_at):
        return None

    config = TranscriberConfig(
        api_key="api-key",
        base_url="https://example.test/v1",
        model="transcription-model",
        prompt="Use speaker labels",
        concurrency=3,
        retry_seconds=17,
        max_retries=4,
        retry_backoff_seconds=[2, 5, 11],
        on_chunk_done=on_chunk_done,
        on_chunk_retry=on_chunk_retry,
    )

    create_transcriber(config)

    assert captured == {
        "api_key": "api-key",
        "base_url": "https://example.test/v1",
        "model": "transcription-model",
        "prompt": "Use speaker labels",
        "concurrency": 3,
        "retry_seconds": 17,
        "max_retries": 4,
        "retry_backoff_seconds": [2, 5, 11],
        "on_chunk_done": on_chunk_done,
        "on_chunk_retry": on_chunk_retry,
    }


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
        "stt_vault.persistence.workspace.worker_repository.db.update_stage",
        lambda *_args: calls.append("stage"),
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.detect_slide_changes",
        lambda *_args, **_kwargs: [{"timestamp": 2.0, "score": 20.0, "kind": "slide_change"}],
    )
    monkeypatch.setattr(
        "stt_vault.persistence.workspace.worker_repository.db.replace_visual_events",
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
    monkeypatch.setattr(
        "stt_vault.persistence.workspace.worker_repository.db.update_stage",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.detect_slide_changes",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "stt_vault.persistence.workspace.worker_repository.db.replace_visual_events",
        lambda *_args: None,
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
        def __init__(self, config: TranscriberConfig):
            assert config.model == "model"
            self.on_chunk_done = config.on_chunk_done

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
        repository=SimpleNamespace(),
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

from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.models.records import AssetRecord, TranscriptSegment
from stt_vault.workers.worker_models import PreparedAsset, TranscriptionWork
from stt_vault.workers.worker_transcription import TranscriberConfig, TranscriptionStage


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

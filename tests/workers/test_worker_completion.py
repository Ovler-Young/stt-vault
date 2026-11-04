from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.workers.worker_completion import CompletionPersistence, CompletionStage
from stt_vault.workers.worker_models import PreparedAsset


class ProviderFailure(Exception):
    __module__ = "openai"


def test_complete_asset_persists_before_generating_summary(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    stage = CompletionStage(
        SimpleNamespace(stt_db_path=Path("app.sqlite3")),
        summary_generator=lambda _settings, asset_id: calls.append(("generate-summary", asset_id)),
    )

    monkeypatch.setattr(
        "stt_vault.persistence.workspace.worker_repository.db.mark_success",
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

    assert [name for name, _payload in calls] == ["asset-success", "generate-summary"]
    assert calls[0][1][1]["transcript_segments"] == [{"text": "hello"}]


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (
            OSError("failed at /srv/private/clip.wav token=secret-value"),
            {"category": "filesystem", "message": "A local processing operation failed"},
        ),
        (
            ProviderFailure("provider token=secret-value at /srv/private/clip.wav"),
            {"category": "provider", "message": "An external provider request failed"},
        ),
    ],
)
def test_partial_completion_persists_classified_failure_without_exception_details(
    error: Exception, expected_error: dict[str, str]
) -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.partial_errors: list[dict[str, str]] = []

        def mark_success(self, _asset_id: str, **_values: object) -> None:
            pass

        def mark_partial(self, _asset_id: str, error: dict[str, str]) -> None:
            self.partial_errors.append(error)

    settings = SimpleNamespace(stt_db_path=Path("app.sqlite3"))
    repository = FakeRepository()
    stage = CompletionStage(
        settings,
        persistence=CompletionPersistence(settings, repository=repository),
    )

    stage.complete_partial(
        "asset-1",
        PreparedAsset(
            wav_path=Path("audio.wav"),
            duration=12.0,
            diarization_stats={},
            raw_segments=[],
            merged_segments=[],
            speaker_centroids={},
        ),
        transcript_segments=[{"text": "partial"}],
        exports={"srt": "asset.srt"},
        error=error,
    )

    assert repository.partial_errors == [expected_error]

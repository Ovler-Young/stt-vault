from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.core.models.records import ErrorRecord, ExportPaths, TranscriptSegment
from stt_vault.workers.worker_completion import CompletionStage
from stt_vault.workers.worker_models import PreparedAsset


class ProviderFailure(Exception):
    __module__ = "openai"


def test_complete_asset_persists_before_generating_summary() -> None:
    calls: list[str] = []
    database = SimpleNamespace(
        complete_asset=lambda _command: calls.append("asset-success"),
        add_event=lambda _event: None,
    )
    stage = CompletionStage(
        SimpleNamespace(),
        database,
        summary_generator=lambda _settings, asset_id, *, database: calls.append("generate-summary"),
    )

    stage.complete(
        "asset-1",
        PreparedAsset(Path("audio.wav"), 12.0, {}, [], [], {}),
        transcript_segments=[TranscriptSegment(0.0, 1.0, "SPEAKER_00", "hello")],
        exports=ExportPaths(srt="asset.srt"),
    )

    assert calls == ["asset-success", "generate-summary"]


@pytest.mark.parametrize("error", [OSError("private"), ProviderFailure("secret")])
def test_partial_completion_uses_the_explicit_database(error: Exception) -> None:
    errors: list[ErrorRecord] = []
    database = SimpleNamespace(
        complete_asset=lambda _command: None,
        mark_partial=lambda _asset_id, value: errors.append(value),
        add_event=lambda _event: None,
    )
    stage = CompletionStage(SimpleNamespace(), database)

    stage.complete_partial(
        "asset-1",
        PreparedAsset(Path("audio.wav"), 12.0, {}, [], [], {}),
        transcript_segments=[TranscriptSegment(0.0, 1.0, "SPEAKER_00", "partial")],
        exports=ExportPaths(srt="asset.srt"),
        error=error,
    )

    assert errors[0].category in {"filesystem", "provider"}

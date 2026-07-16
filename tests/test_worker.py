from pathlib import Path
from types import SimpleNamespace

from stt_vault.worker import Worker


def test_complete_asset_publishes_summary_state_and_generates_summary(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    worker = object.__new__(Worker)
    worker.settings = SimpleNamespace(stt_db_path=Path("app.sqlite3"))

    monkeypatch.setattr(
        "stt_vault.worker.db.update_asset_summary",
        lambda _db_path, asset_id, **kwargs: calls.append(("summary-state", (asset_id, kwargs))),
    )
    monkeypatch.setattr(
        "stt_vault.worker.db.mark_success",
        lambda _db_path, asset_id, **kwargs: calls.append(("asset-success", (asset_id, kwargs))),
    )
    monkeypatch.setattr(
        worker,
        "generate_summary",
        lambda asset_id: calls.append(("generate-summary", asset_id)),
    )

    worker.complete_asset(
        "asset-1",
        wav_path=Path("audio.wav"),
        duration=12.0,
        diarization_stats={},
        raw_segments=[],
        merged_segments=[],
        speaker_centroids={},
        transcript_segments=[{"text": "hello"}],
        exports={"srt": "asset.srt"},
    )

    assert [name for name, _payload in calls] == [
        "summary-state",
        "asset-success",
        "generate-summary",
    ]
    assert calls[0][1] == ("asset-1", {"status": "running"})
    assert calls[1][1][1]["transcript_segments"] == [{"text": "hello"}]

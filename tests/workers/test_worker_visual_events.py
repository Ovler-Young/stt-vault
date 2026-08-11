from pathlib import Path
from types import SimpleNamespace

from stt_vault.core.models.records import AssetRecord, ExportPaths
from stt_vault.workers.worker_exports import VisualEventStage


def test_visual_event_stage_persists_and_exports_video_events(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    database = SimpleNamespace(
        update_stage=lambda **_kwargs: calls.append("stage"),
        replace_visual_events=lambda _asset_id, _events: calls.append("persist"),
        add_event=lambda _event: None,
    )
    stage = VisualEventStage(
        SimpleNamespace(
            exports_dir=tmp_path / "exports",
            visual_sample_interval_seconds=2.0,
            visual_change_threshold=18.0,
            visual_min_gap_seconds=6.0,
        ),
        database,
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.detect_slide_changes",
        lambda *_args, **_kwargs: [{"timestamp": 2.0, "score": 20.0, "kind": "slide_change"}],
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.write_visual_event_thumbnails",
        lambda *_args, **_kwargs: calls.append("thumbnails"),
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.write_visual_events_export", lambda *_args: "events.json"
    )

    exports = stage.detect(
        "asset-1",
        AssetRecord("asset-1", "clip.mp4", "video", str(tmp_path / "clip.mp4"), "processing", 1, 1),
    )

    assert calls == ["stage", "persist", "thumbnails"]
    assert exports == ExportPaths(visual_events="events.json")


def test_visual_event_stage_propagates_injected_thumbnail_extractor(
    monkeypatch, tmp_path: Path
) -> None:
    def extractor(_media_path: Path, output_path: Path, _timestamp: float, _runner: object) -> Path:
        return output_path

    def runner(_command: list[str]) -> object:
        raise AssertionError("the injected extractor must receive the runner")

    database = SimpleNamespace(
        update_stage=lambda **_kwargs: None,
        replace_visual_events=lambda _asset_id, _events: None,
        add_event=lambda _event: None,
    )
    stage = VisualEventStage(
        SimpleNamespace(
            exports_dir=tmp_path / "exports",
            visual_sample_interval_seconds=2.0,
            visual_change_threshold=18.0,
            visual_min_gap_seconds=6.0,
        ),
        database,
        thumbnail_runner=runner,
        thumbnail_extractor=extractor,
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.detect_slide_changes", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.write_visual_event_thumbnails",
        lambda *_args, **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(
        "stt_vault.workers.worker_exports.write_visual_events_export", lambda *_args: "events.json"
    )

    stage.detect(
        "asset-1",
        AssetRecord("asset-1", "clip.mp4", "video", str(tmp_path / "clip.mp4"), "processing", 1, 1),
    )

    assert captured["extractor"] is extractor
    assert captured["runner"] is runner

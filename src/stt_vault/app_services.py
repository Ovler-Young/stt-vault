import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from . import db
from .diarization import match_speakers
from .exports import write_exports
from .settings import Settings
from .visual import (
    detect_slide_changes,
    write_visual_event_thumbnails,
    write_visual_events_export,
)

__all__ = [
    "clean_display_name",
    "count_local_speaker_segments",
    "detect_asset_visual_events",
    "recompute_asset_speaker_matches",
    "resolve_speaker_id",
    "rewrite_asset_exports",
    "stream_process_stdout",
]


def clean_display_name(display_name: str) -> str:
    value = display_name.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Display name is required")
    if len(value) > 120:
        raise HTTPException(status_code=400, detail="Display name is too long")
    return value


def resolve_speaker_id(
    settings: Settings,
    asset: dict,
    local_speaker: str,
    display_name: str,
) -> str:
    for segment in asset.get("transcript_segments") or []:
        if segment.get("speaker") != local_speaker:
            continue
        speaker_id = segment.get("speaker_id")
        if speaker_id and speaker_id != local_speaker:
            return speaker_id

    existing = db.find_speaker_by_display_name(settings.stt_db_path, display_name)
    if existing is not None:
        return existing["id"]

    return f"spk_{uuid4().hex[:12]}"


def count_local_speaker_segments(asset: dict, local_speaker: str) -> int:
    return max(
        1,
        sum(
            1
            for segment in asset.get("transcript_segments") or []
            if segment["speaker"] == local_speaker
        ),
    )


def rewrite_asset_exports(settings: Settings, asset_ids: list[str]) -> None:
    for asset_id in asset_ids:
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            continue

        transcript_segments = asset.get("transcript_segments") or []
        raw_segments = asset.get("raw_segments") or []
        if not transcript_segments or not raw_segments:
            continue

        exports = write_exports(
            settings.exports_dir,
            asset_id,
            asset["filename"],
            transcript_segments,
            raw_segments,
            settings.parsed_export_formats,
        )
        db.update_asset_exports(settings.stt_db_path, asset_id, exports)


def detect_asset_visual_events(settings: Settings, asset: dict) -> list[dict]:
    if asset.get("media_type") != "video":
        return []

    events = detect_slide_changes(
        Path(asset["original_path"]),
        sample_interval_seconds=settings.visual_sample_interval_seconds,
        threshold=settings.visual_change_threshold,
        min_gap_seconds=settings.visual_min_gap_seconds,
    )
    db.replace_visual_events(settings.stt_db_path, asset["id"], events)
    write_visual_event_thumbnails(
        Path(asset["original_path"]),
        settings.exports_dir,
        asset["id"],
        events,
    )
    exports = dict(asset.get("exports") or {})
    exports["visual_events"] = write_visual_events_export(
        settings.exports_dir,
        asset["id"],
        events,
    )
    db.update_asset_exports(settings.stt_db_path, asset["id"], exports)
    return events


def recompute_asset_speaker_matches(settings: Settings, asset_ids: list[str]) -> list[str]:
    updated_asset_ids = []
    known_speakers = db.list_speakers(settings.stt_db_path)
    for asset_id in dict.fromkeys(asset_ids):
        asset = db.get_asset(settings.stt_db_path, asset_id)
        if asset is None:
            continue

        centroids = asset.get("speaker_centroids") or {}
        transcript_segments = asset.get("transcript_segments") or []
        if not centroids or not transcript_segments:
            continue

        matches = match_speakers(
            centroids,
            known_speakers,
            settings.speaker_similarity_threshold,
        )
        db.relabel_asset_speakers(settings.stt_db_path, asset_id, matches)
        updated_asset_ids.append(asset_id)
    return updated_asset_ids


def stream_process_stdout(command: list[str]) -> Iterator[bytes]:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        if process.stdout is None:
            return
        while True:
            chunk = process.stdout.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.stdout is not None:
            process.stdout.close()

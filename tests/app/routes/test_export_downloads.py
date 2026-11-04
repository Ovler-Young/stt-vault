from pathlib import Path

import pytest
from _support.upload_routes import auth_headers
from fastapi.testclient import TestClient

from stt_vault.core.config import get_settings
from stt_vault.persistence import db

EXPORT_FILES = {
    "json": "transcript.json",
    "whisper_json": "whisper_like.json",
    "ai_text": "transcript.ai.txt",
    "srt": "transcript.srt",
    "vtt": "transcript.vtt",
    "hyperaudio_html": "hyperaudio.html",
    "rttm": "speakers.rttm",
}


def create_export_asset(
    asset_id: str,
    *,
    filename: str,
    title: str | None = None,
) -> None:
    settings = get_settings()
    exports = {}
    for format_name, export_filename in EXPORT_FILES.items():
        export_path = settings.exports_dir / asset_id / export_filename
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text("transcript", encoding="utf-8")
        exports[format_name] = str(export_path)
    db.create_asset(
        settings.stt_db_path,
        asset_id,
        filename,
        "video",
        Path(f"/{filename}"),
    )
    if title is not None:
        db.update_asset_summary(
            settings.stt_db_path,
            asset_id,
            status="success",
            title=title,
        )
    db.update_asset_exports(settings.stt_db_path, asset_id, exports)


def test_export_download_preserves_dotted_title_and_all_export_suffixes(
    client: TestClient,
) -> None:
    create_export_asset("asset-1", filename="recording.mp4", title="Release v1.2")

    for format_name, export_filename in EXPORT_FILES.items():
        response = client.get(
            f"/api/assets/asset-1/exports/{format_name}", headers=auth_headers(client)
        )

        assert response.status_code == 200
        assert response.headers["content-disposition"] == (
            f"attachment; filename*=utf-8''Release%20v1.2{''.join(Path(export_filename).suffixes)}"
        )


@pytest.mark.parametrize(
    ("asset_id", "title", "expected_stem"),
    [
        ("missing-title", None, "source.name"),
        ("empty-title", "", "source.name"),
        ("dot-title", ".", "upload"),
    ],
)
def test_export_download_uses_source_fallback_or_safe_title_basename(
    client: TestClient,
    asset_id: str,
    title: str | None,
    expected_stem: str,
) -> None:
    create_export_asset(asset_id, filename="source.name.mp4", title=title)

    response = client.get(f"/api/assets/{asset_id}/exports/ai_text", headers=auth_headers(client))

    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{expected_stem}.ai.txt"'
    )

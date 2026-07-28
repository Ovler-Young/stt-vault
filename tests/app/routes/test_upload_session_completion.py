from pathlib import Path
from types import SimpleNamespace

import pytest

from stt_vault.services.upload_sessions import UploadSessionDependencies, UploadSessionService


def test_upload_session_completion_restores_temp_file_when_database_write_fails(
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        stt_db_path=tmp_path / "app.sqlite3",
        uploads_dir=tmp_path / "uploads",
        media_dir=tmp_path / "media",
    )
    temp_path = settings.uploads_dir / "upload.part"
    temp_path.parent.mkdir(parents=True)
    temp_path.write_bytes(b"upload")
    upload = {
        "id": "upload-1",
        "filename": "clip.wav",
        "total_size": 6,
        "offset": 6,
        "temp_path": str(temp_path),
    }
    stored_path = settings.media_dir / "asset-1" / "original.wav"
    stored_path.parent.mkdir(parents=True)

    dependencies = UploadSessionDependencies(
        create_upload_session=lambda *_args: upload,
        get_upload_session=lambda *_args: upload,
        update_upload_offset=lambda *_args: None,
        complete_upload_session=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        ),
        move_upload=lambda *_args: ("asset-1", stored_path, "audio"),
    )
    stored_path.write_bytes(b"upload")

    with pytest.raises(RuntimeError, match="database unavailable"):
        UploadSessionService(settings, dependencies).complete("upload-1")

    assert temp_path.read_bytes() == b"upload"
    assert not (settings.media_dir / "asset-1").exists()

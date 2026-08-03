from pathlib import Path

import pytest

from stt_vault.services import media_storage
from stt_vault.services.media_storage import move_upload, store_upload


@pytest.mark.parametrize(
    ("filename", "detected_type"),
    [("recording.unknown", "audio"), ("recording.m4a", "video")],
)
def test_upload_storage_classifies_bytes_instead_of_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    detected_type: str,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"media")
    monkeypatch.setattr(media_storage, "ffprobe_media_type", lambda _path: detected_type)

    asset_id, stored_path, media_type = store_upload(tmp_path / "media", filename, source)

    assert stored_path == tmp_path / "media" / asset_id / filename
    assert media_type == detected_type


@pytest.mark.parametrize("operation", [store_upload, move_upload])
def test_upload_storage_leaves_no_asset_directory_when_probe_fails(
    operation,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"not media")
    monkeypatch.setattr(
        media_storage,
        "ffprobe_media_type",
        lambda _path: (_ for _ in ()).throw(ValueError("invalid media")),
    )

    with pytest.raises(ValueError, match="invalid media"):
        operation(tmp_path / "media", "recording.bin", source)

    assert source.exists()
    assert not (tmp_path / "media").exists()


@pytest.mark.parametrize("operation", [store_upload, move_upload])
def test_upload_storage_uses_a_single_destination_and_metadata_setup(
    operation, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(media_storage, "ffprobe_media_type", lambda _path: "audio")

    asset_id, stored_path, media_type = operation(tmp_path / "media", "clip.wav", source)

    assert stored_path == tmp_path / "media" / asset_id / "clip.wav"
    assert stored_path.read_bytes() == b"audio"
    assert media_type == "audio"

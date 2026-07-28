from pathlib import Path

import pytest

from stt_vault.services.media_storage import move_upload, store_upload


@pytest.mark.parametrize("operation", [store_upload, move_upload])
def test_upload_storage_uses_a_single_destination_and_metadata_setup(
    operation, tmp_path: Path
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")

    asset_id, stored_path, media_type = operation(tmp_path / "media", "clip.wav", source)

    assert stored_path == tmp_path / "media" / asset_id / "clip.wav"
    assert stored_path.read_bytes() == b"audio"
    assert media_type == "audio"

from pathlib import Path

import pytest
from _support.upload_routes import auth_headers, create_test_app
from fastapi.testclient import TestClient

from stt_vault.core.config import get_settings


def test_upload_size_limit_uses_one_megabyte_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    app = create_test_app(monkeypatch, tmp_path)
    test_client = TestClient(app)
    try:
        headers = auth_headers(test_client)
        direct_upload = test_client.post(
            "/api/assets",
            headers=headers,
            files={"file": ("too-large.wav", b"x" * (1024 * 1024 + 1), "audio/wav")},
        )
        exact_limit = test_client.post(
            "/api/uploads",
            headers=headers,
            json={"filename": "limit.wav", "size": 1024 * 1024},
        )
        over_limit = test_client.post(
            "/api/uploads",
            headers=headers,
            json={"filename": "too-large.wav", "size": 1024 * 1024 + 1},
        )
    finally:
        test_client.close()
        get_settings.cache_clear()

    assert direct_upload.status_code == 413
    assert direct_upload.json() == {"detail": "Upload is too large"}
    assert exact_limit.status_code == 200
    assert over_limit.status_code == 413

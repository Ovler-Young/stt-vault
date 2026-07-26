from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from stt_vault.core import static_frontend


def test_mount_static_frontend_uses_package_static_directory(monkeypatch, tmp_path: Path) -> None:
    module_path = tmp_path / "core" / "static_frontend.py"
    module_path.parent.mkdir()
    static_dir = tmp_path / "static"
    (static_dir / "_app").mkdir(parents=True)
    (static_dir / "index.html").write_text("frontend")
    monkeypatch.setattr(static_frontend, "__file__", str(module_path))

    app = FastAPI()
    static_frontend.mount_static_frontend(app)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.text == "frontend"

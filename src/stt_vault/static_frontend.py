from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

__all__ = ["mount_static_frontend"]


def mount_static_frontend(app: FastAPI) -> None:
    static_dir = Path(__file__).parent / "static"
    if not static_dir.exists():
        return

    static_root = static_dir.resolve()
    app.mount("/_app", StaticFiles(directory=static_root / "_app"), name="static")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")

        candidate = (static_root / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(static_root):
            return FileResponse(candidate)

        index_path = static_root / "200.html"
        if not index_path.exists():
            index_path = static_root / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Frontend not built")
        return FileResponse(index_path)

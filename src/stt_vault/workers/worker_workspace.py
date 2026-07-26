import shutil
from pathlib import Path


class ProcessingWorkspace:
    def __init__(self, root: Path, asset_id: str) -> None:
        self.path = root / asset_id

    def __enter__(self) -> Path:
        if self.path.exists():
            shutil.rmtree(self.path)
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, *_: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

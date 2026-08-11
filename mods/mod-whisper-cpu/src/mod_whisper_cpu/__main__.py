"""Container entry point for the CPU Mod."""

import os
from pathlib import Path

import uvicorn

from .app import create_app
from .engine import WhisperCppServerEngine


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    engine = WhisperCppServerEngine(
        root / "model-manifest.json",
        os.environ.get("WHISPER_MODEL_ID", "ggml-base.en.bin"),
        Path(os.environ.get("WHISPER_MODELS_DIR", "/models")),
    )
    uvicorn.run(
        create_app(
            engine=engine,
            token_path=Path("/run/secrets/stt_mod_token"),
            image_digest=os.environ["WHISPER_IMAGE_DIGEST"],
        ),
        host="0.0.0.0",
        port=8081,
    )


if __name__ == "__main__":
    main()

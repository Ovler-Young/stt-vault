"""Install exactly one manifest-pinned whisper model during the image build."""

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path


def main() -> None:
    manifest_path = Path(sys.argv[1])
    destination_dir = Path(sys.argv[2])
    model_id = os.environ["WHISPER_MODEL_ID"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        model = manifest["models"][model_id]
    except KeyError as error:
        raise SystemExit("WHISPER_MODEL_ID is not declared in model-manifest.json") from error
    if model["id"] != model_id:
        raise SystemExit("model manifest identity does not match WHISPER_MODEL_ID")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / model_id
    with (
        urllib.request.urlopen(model["url"], timeout=60) as source,
        destination.open("wb") as output,
    ):
        for block in iter(lambda: source.read(1024 * 1024), b""):
            output.write(block)
    with destination.open("rb") as artifact:
        digest = hashlib.file_digest(artifact, "sha256").hexdigest()
    if digest != model["sha256"]:
        destination.unlink(missing_ok=True)
        raise SystemExit("selected model checksum did not match model-manifest.json")


if __name__ == "__main__":
    main()

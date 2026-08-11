"""Persistent, selected-model-only whisper.cpp server adapter."""

import hashlib
import io
import json
import subprocess
import threading
import time
import urllib.request
import wave
from contextlib import nullcontext
from pathlib import Path


class WhisperCppServerEngine:
    """Runs one CPU-only server whose model was verified while the image was built."""

    def __init__(
        self, manifest_path: Path, model_id: str, models_dir: Path, port: int = 8178
    ) -> None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        try:
            selected = manifest["models"][model_id]
        except KeyError as error:
            raise ValueError("WHISPER_MODEL_ID is not declared in the model manifest") from error
        self.identity = {
            key: selected[key]
            for key in ("id", "revision", "sha256", "license_ref", "access_declaration")
        }
        self._models_dir = models_dir
        self._port = port
        self._process: subprocess.Popen[bytes] | None = None
        self._process_lock = threading.Lock()
        self._generation = 0
        self._load_count = 0
        self.readiness = "loading"

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def load_count(self) -> int:
        return self._load_count

    def load(self) -> None:
        with self._process_lock:
            if self._is_running() and self.readiness == "ready":
                return
            self._terminate_locked()
            self.readiness = "loading"
            try:
                model_path = self._selected_model_path()
                self._process = subprocess.Popen(
                    [
                        "whisper-server",
                        "-m",
                        str(model_path),
                        "-ng",
                        "-1",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(self._port),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._wait_until_ready_locked()
                self._generation += 1
                self._load_count += 1
                self.readiness = "ready"
            except Exception:
                self._terminate_locked()
                self.readiness = "failed"
                raise

    def restart(self) -> None:
        self.load()

    def transcribe(self, audio_path: Path, request: dict[str, object]) -> dict[str, object]:
        if self.readiness != "ready":
            raise RuntimeError("whisper-server is not ready")
        boundary = "stt-whisper-cpu"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="file"; filename="chunk.wav"\r\n',
                b"Content-Type: audio/wav\r\n\r\n",
                audio_path.read_bytes(),
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        response = self._request(
            "POST", "/inference", body, f"multipart/form-data; boundary={boundary}"
        )
        payload = json.loads(response)
        text = str(payload.get("text", "")).strip()
        duration = float(request["chunk"]["end"]) - float(request["chunk"]["start"])
        return {
            "kind": "speech" if text else "no_speech",
            "segments": [{"start": 0.0, "end": duration, "text": text}] if text else [],
        }

    def cancel_active(self) -> None:
        """Interrupt the resident process so its only active inference cannot continue."""
        with self._process_lock:
            self.readiness = "loading"
            self._terminate_locked()

    def close(self) -> None:
        lock = getattr(self, "_process_lock", None)
        with lock if lock is not None else nullcontext():
            self._terminate_locked()
            self.readiness = "failed"

    def _selected_model_path(self) -> Path:
        model_path = self._models_dir / self.identity["id"]
        if not model_path.is_file() or _sha256(model_path) != self.identity["sha256"]:
            raise RuntimeError("selected model is missing or does not match the image manifest")
        return model_path

    def _wait_until_ready_locked(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not self._is_running():
                raise RuntimeError("whisper-server exited during startup")
            try:
                self._request("GET", "/")
            except OSError:
                time.sleep(0.2)
            else:
                self._self_check()
                return
        raise RuntimeError("whisper-server did not become ready")

    def _is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _terminate_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    def _self_check(self) -> None:
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(b"\0\0" * 160)
        boundary = "stt-whisper-self-check"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="file"; filename="check.wav"\r\n',
                b"Content-Type: audio/wav\r\n\r\n",
                audio.getvalue(),
                f"\r\n--{boundary}--\r\n".encode(),
            ]
        )
        json.loads(
            self._request("POST", "/inference", body, f"multipart/form-data; boundary={boundary}")
        )

    def _request(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> bytes:
        headers = {"Content-Type": content_type} if content_type else {}
        request = urllib.request.Request(
            f"http://127.0.0.1:{self._port}{path}", data=body, headers=headers, method=method
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

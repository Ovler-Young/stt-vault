import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = Path("/run/secrets/stt_mod_token").read_text(encoding="utf-8").strip()
MOD = {
    "id": "mod-whisper-cpu",
    "version": "0.0.0-e2e",
    "image_digest": "sha256:" + "a" * 64,
    "runtime": "fixture",
    "model": {
        "id": "fixture-timed-units",
        "revision": "e2e",
        "sha256": "0" * 64,
        "license_ref": "test-fixture",
        "access_declaration": "fixture",
    },
}
REQUESTS = 0


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/livez":
            return self.send_json(200, {"status": "live"})
        if not self.authorized():
            return self.error_response(401, "mod authentication failed")
        if self.path == "/readyz":
            return self.send_json(200, {"status": "ready", "model": MOD["model"], "rss_mb": 1})
        if self.path == "/v1/capabilities":
            timed = REQUESTS % 2 == 0
            result = {
                "offerings": [{"model_id": "fixture-timed-units", "device_id": "cpu"}],
                "max_audio_bytes": 26214400,
                "max_audio_seconds": 120,
                "readiness": "ready",
            }
            if timed:
                result["transcription"] = {
                    "timed_units": {
                        "unit_kinds": ["word", "punctuation"],
                        "time_base": "chunk_ms",
                        "precision_ms": 50,
                    }
                }
            return self.success(result)
        self.send_error(404)

    def do_POST(self) -> None:
        global REQUESTS
        if not self.authorized():
            return self.error_response(401, "mod authentication failed")
        if self.path.startswith("/v1/cancellations/"):
            self.send_response(204)
            self.end_headers()
            return
        if self.path != "/v1/transcriptions":
            self.send_error(404)
            return
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        timed = REQUESTS % 2 == 0
        REQUESTS += 1
        result = {
            "kind": "speech",
            "segments": [{"start": 0.0, "end": 1.0, "text": "fixture unit"}],
        }
        if timed:
            result["timed_units"] = [
                {
                    "unit_index": 0,
                    "text": "fixture unit",
                    "start_ms": 50,
                    "end_ms": 150,
                    "confidence": None,
                    "language": "en",
                    "token_kind": "word",
                },
                {
                    "unit_index": 1,
                    "text": "longunbrokenfixturetext" * 12,
                    "start_ms": 150,
                    "end_ms": 200,
                    "confidence": None,
                    "language": "en",
                    "token_kind": "word",
                },
            ]
        self.success(result)

    def authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def success(self, result: dict) -> None:
        self.send_json(
            200,
            {
                "contract_version": "v1",
                "correlation_id": "00000000-0000-4000-8000-000000000001",
                "mod": MOD,
                "result": result,
            },
        )

    def error_response(self, status: int, message: str) -> None:
        self.send_json(
            status,
            {
                "contract_version": "v1",
                "correlation_id": "00000000-0000-4000-8000-000000000001",
                "mod": MOD,
                "error": {"category": "invalid_request", "message": message, "retryable": False},
            },
        )

    def send_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return None


ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()

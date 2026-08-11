"""Exercise the authenticated whisper CPU Mod contract from inside its network namespace."""

import hashlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

TOKEN = open("/run/secrets/stt_mod_token", encoding="utf-8").read().strip()
EXPECTED_MODEL = os.environ["EXPECTED_MODEL"]
FIXTURE_SHA256 = "2976da01e205a110c9fa41d47659e238a5c6d3c3f3137582f2949853faa201dd"


def request(
    path: str,
    *,
    authenticated: bool,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> object:
    headers = dict(headers or {})
    if authenticated and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if body is not None:
        headers["Content-Type"] = "multipart/form-data; boundary=smoke-boundary"
    request_value = urllib.request.Request(
        f"http://127.0.0.1:8081{path}",
        body,
        headers=headers,
        method=method or ("POST" if body else "GET"),
    )
    return urllib.request.urlopen(request_value, timeout=30)


try:
    request("/readyz", authenticated=False)
except urllib.error.HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError("readyz accepted a request without the Mod token")

with request("/livez", authenticated=False) as response:
    assert json.load(response) == {"status": "live"}

for path in ("/readyz", "/v1/capabilities"):
    try:
        request(path, authenticated=True, headers={"Authorization": "Bearer invalid"})
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError(f"{path} accepted an invalid Mod token")

with request("/readyz", authenticated=True) as response:
    ready = json.load(response)
assert ready["status"] == "ready"
assert ready["model"]["id"] == EXPECTED_MODEL
assert set(ready["model"]) == {"id", "revision", "sha256"}
assert 0 <= ready["rss_mb"] <= 4096

with request("/v1/capabilities", authenticated=True) as response:
    capabilities = json.load(response)
assert capabilities["mod"]["model"]["id"] == EXPECTED_MODEL
assert capabilities["result"]["offerings"] == [{"model_id": EXPECTED_MODEL, "device_id": "cpu"}]

audio = io.BytesIO()
with wave.open(audio, "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(16_000)
    wav.writeframes(b"\0\0" * 1_600)
assert hashlib.sha256(audio.getvalue()).hexdigest() == FIXTURE_SHA256
boundary = b"smoke-boundary"
metadata = json.dumps(
    {
        "contract_version": "v1",
        "correlation_id": str(uuid.uuid4()),
        "idempotency_key": str(uuid.uuid4()),
        "asset_id": "smoke:tiny",
        "chunk": {"index": 0, "start": 0.0, "end": 0.1, "speaker_id": "speaker:smoke"},
        "language": "en",
        "prompt": None,
    }
).encode()


def transcription_body(idempotency_key: uuid.UUID, audio_bytes: bytes, duration: float) -> bytes:
    request_metadata = json.loads(metadata)
    request_metadata["idempotency_key"] = str(idempotency_key)
    request_metadata["chunk"]["end"] = duration
    return b"\r\n".join(
        [
            b"--" + boundary,
            b'Content-Disposition: form-data; name="request"',
            b"Content-Type: application/json",
            b"",
            json.dumps(request_metadata).encode(),
            b"--" + boundary,
            b'Content-Disposition: form-data; name="audio"; filename="smoke.wav"',
            b"Content-Type: audio/wav",
            b"",
            audio_bytes,
            b"--" + boundary + b"--",
            b"",
        ]
    )


def transcribe(
    idempotency_key: uuid.UUID, audio_bytes: bytes | None = None, duration: float = 0.1
) -> tuple[object, dict[str, str]]:
    with request(
        "/v1/transcriptions",
        authenticated=True,
        body=transcription_body(idempotency_key, audio_bytes or audio.getvalue(), duration),
    ) as response:
        return json.load(response), {
            header: response.headers[header]
            for header in (
                "X-Mod-Engine-Pid",
                "X-Mod-Engine-Generation",
                "X-Mod-Engine-Load-Count",
            )
        }


transcription, first_headers = transcribe(uuid.uuid4())
assert transcription["contract_version"] == "v1"
assert transcription["mod"]["model"]["id"] == EXPECTED_MODEL
assert transcription["result"] == {"kind": "no_speech", "segments": []}
for segment in transcription["result"]["segments"]:
    assert set(segment) == {"start", "end", "text"}
    assert 0 <= segment["start"] < segment["end"] <= 0.15

resident_transcription, resident_headers = transcribe(uuid.uuid4())
assert resident_transcription["result"] == {"kind": "no_speech", "segments": []}
assert resident_headers == first_headers
assert int(first_headers["X-Mod-Engine-Pid"]) > 1
assert int(first_headers["X-Mod-Engine-Generation"]) >= 1
assert first_headers["X-Mod-Engine-Load-Count"] == "1"

cancel_key = uuid.uuid4()
cancelled: list[object] = []
cancel_audio = io.BytesIO()
with wave.open(cancel_audio, "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(16_000)
    wav.writeframes(b"\0\0" * (16_000 * 120))


def cancelled_transcription() -> None:
    try:
        cancelled.append(transcribe(cancel_key, cancel_audio.getvalue(), 120.0))
    except urllib.error.HTTPError as error:
        assert error.code == 503
        cancelled.append(error)


in_flight = threading.Thread(target=cancelled_transcription)
in_flight.start()
time.sleep(0.2)
with request(f"/v1/cancellations/{cancel_key}", authenticated=True, method="POST") as cancellation:
    assert cancellation.code == 204
in_flight.join(timeout=30)
assert not in_flight.is_alive()
assert len(cancelled) == 1

for _ in range(60):
    try:
        post_cancellation_transcription, post_cancellation_headers = transcribe(uuid.uuid4())
    except urllib.error.HTTPError as error:
        assert error.code == 503
        time.sleep(0.5)
    else:
        break
else:
    raise AssertionError("engine did not recover after cancellation")
assert post_cancellation_transcription["result"] == {"kind": "no_speech", "segments": []}
assert int(post_cancellation_headers["X-Mod-Engine-Pid"]) != int(first_headers["X-Mod-Engine-Pid"])
assert int(post_cancellation_headers["X-Mod-Engine-Generation"]) > int(
    first_headers["X-Mod-Engine-Generation"]
)
assert int(post_cancellation_headers["X-Mod-Engine-Load-Count"]) > int(
    first_headers["X-Mod-Engine-Load-Count"]
)

assert not [path for path in os.listdir("/tmp") if path.startswith("stt-whisper-")]

"""Exercise the selected CPU Mod through the application's internal job path."""

import json
import mimetypes
import os
import time
import urllib.request
import uuid
from pathlib import Path

IN_PROGRESS_STATES = {"queued", "processing"}
SUCCESS_STATE = "success"
APP_BASE_URL = os.environ["APP_BASE_URL"]


def request(
    path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None
) -> object:
    request_value = urllib.request.Request(
        f"{APP_BASE_URL}{path}",
        data=body,
        headers=headers or {},
        method="POST" if body is not None else "GET",
    )
    return urllib.request.urlopen(request_value, timeout=30)


with request(
    "/api/auth/token",
    body=b'{"password":"smoke-admin-password"}',
    headers={"Content-Type": "application/json"},
) as response:
    token = json.load(response)["access_token"]

audio_path = Path("/tmp/jfk.wav")
audio = audio_path.read_bytes()
boundary = f"stt-vault-smoke-{uuid.uuid4().hex}".encode()
body = b"\r\n".join(
    [
        b"--" + boundary,
        b'Content-Disposition: form-data; name="file"; filename="jfk.wav"',
        f"Content-Type: {mimetypes.guess_type(audio_path.name)[0] or 'audio/wav'}".encode(),
        b"",
        audio,
        b"--" + boundary + b"--",
        b"",
    ]
)
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
}
with request("/api/assets", body=body, headers=headers) as response:
    uploaded = json.load(response)
assert uploaded["status"] == "queued"
asset_id = uploaded["id"]

deadline = time.monotonic() + 240
detail: dict[str, object] = {}
while time.monotonic() < deadline:
    with request(
        f"/api/assets/{asset_id}", headers={"Authorization": f"Bearer {token}"}
    ) as response:
        detail = json.load(response)
    asset_status = detail.get("status")
    job = detail.get("job")
    job_status = job.get("status") if isinstance(job, dict) else None
    if asset_status == SUCCESS_STATE and job_status == SUCCESS_STATE:
        break
    if asset_status in IN_PROGRESS_STATES and job_status in IN_PROGRESS_STATES:
        time.sleep(0.5)
        continue
    raise AssertionError(
        "terminal failure or inconsistent asset/job state: "
        f"asset={asset_status!r} job={job_status!r} detail={detail}"
    )
else:
    raise AssertionError(f"job did not finish: {detail}")

assert detail.get("status") == SUCCESS_STATE, detail
assert isinstance(detail.get("job"), dict)
assert detail["job"].get("status") == SUCCESS_STATE, detail
assert isinstance(detail.get("transcript_segments"), list)
assert detail["transcript_segments"], detail
for segment in detail["transcript_segments"]:
    assert segment.get("timed_units") in (None, [])

expected_provider_id = os.environ["EXPECTED_PROVIDER_ID"]
expected_image_digest = os.environ["EXPECTED_IMAGE_DIGEST"]
completed_providers = detail.get("completed_transcription_providers")
assert isinstance(completed_providers, list) and len(completed_providers) == 1, detail
completed_provider = completed_providers[0]
assert isinstance(completed_provider, dict), detail
assert completed_provider.get("provider_id") == expected_provider_id, detail
assert completed_provider.get("image_digest") == expected_image_digest, detail
completed_count = completed_provider.get("completed_invocation_count")
assert isinstance(completed_count, int) and not isinstance(completed_count, bool), detail
assert completed_count > 0, detail

Path(os.environ["SMOKE_EVIDENCE_PATH"]).write_text(
    json.dumps(
        {
            "asset_id": asset_id,
            "asset_status": detail["status"],
            "job_status": detail["job"]["status"],
            "transcript_segment_count": len(detail["transcript_segments"]),
            "completed_transcription_providers": completed_providers,
        }
    ),
    encoding="utf-8",
)

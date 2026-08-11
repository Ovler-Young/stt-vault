"""Exercise the selected CPU Mod through the application's authenticated job path."""

import json
import mimetypes
import os
import sqlite3
import time
import urllib.request
import uuid
from pathlib import Path

IN_PROGRESS_STATES = {"queued", "processing"}
SUCCESS_STATE = "success"
EXPECTED_PROVIDER_ID = os.environ["EXPECTED_PROVIDER_ID"]
EXPECTED_IMAGE_DIGEST = os.environ["EXPECTED_IMAGE_DIGEST"]


def request(
    path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None
) -> object:
    request_value = urllib.request.Request(
        f"http://127.0.0.1:8080{path}",
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

database_path = Path("/data/app.sqlite3")
assert database_path.is_file()
assert detail.get("status") == SUCCESS_STATE, detail
assert isinstance(detail.get("job"), dict)
assert detail["job"].get("status") == SUCCESS_STATE, detail
assert isinstance(detail.get("transcript_segments"), list)
assert detail["transcript_segments"], detail
for segment in detail["transcript_segments"]:
    assert segment.get("timed_units") in (None, [])

with sqlite3.connect("/data/app.sqlite3") as connection:
    ledger_rows = connection.execute(
        """SELECT work_item.provider_id, work_item.image_digest, work_item.state,
        invocation.state FROM provider_work_items AS work_item
        JOIN provider_invocations AS invocation
          ON invocation.work_item_id = work_item.work_item_id
        WHERE work_item.asset_id = ? AND work_item.role = 'transcription'
          AND work_item.provider_id = ? AND work_item.image_digest = ?""",
        (asset_id, EXPECTED_PROVIDER_ID, EXPECTED_IMAGE_DIGEST),
    ).fetchall()

assert ledger_rows, f"missing transcription provider ledger rows for {asset_id}"
assert all(
    provider_id == EXPECTED_PROVIDER_ID
    and image_digest == EXPECTED_IMAGE_DIGEST
    and work_state == "completed"
    and invocation_state == "completed"
    for provider_id, image_digest, work_state, invocation_state in ledger_rows
), ledger_rows

Path("/tmp/app-smoke-evidence.json").write_text(
    json.dumps(
        {
            "asset_id": asset_id,
            "asset_status": detail["status"],
            "job_status": detail["job"]["status"],
            "provider_id": EXPECTED_PROVIDER_ID,
            "image_digest": EXPECTED_IMAGE_DIGEST,
            "ledger_rows": len(ledger_rows),
        }
    ),
    encoding="utf-8",
)

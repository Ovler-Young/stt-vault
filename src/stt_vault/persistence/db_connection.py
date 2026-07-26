import json
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from stt_vault.core.api_models import JsonValue

AssetJsonFields = Mapping[str, type[object]]

ASSET_JSON_FIELDS: AssetJsonFields = {
    "diarization_stats": dict,
    "raw_segments": list,
    "merged_segments": list,
    "speaker_centroids": dict,
    "transcript_segments": list,
    "exports": dict,
    "error": dict,
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> int:
    return int(time.time())


def decode_record(
    row: sqlite3.Row | None,
    *,
    json_fields: AssetJsonFields | None = None,
) -> dict[str, JsonValue] | None:
    if row is None:
        return None
    data = dict(row)
    for key, expected_type in (json_fields or {}).items():
        value = data.get(key)
        if value is None:
            continue
        decoded = json.loads(value)
        if not isinstance(decoded, expected_type):
            expected_name = expected_type.__name__
            raise ValueError(f"{key} must decode to {expected_name}")
        data[key] = decoded
    return data


def row_to_dict(row: sqlite3.Row | None) -> dict[str, JsonValue] | None:
    return decode_record(row, json_fields=ASSET_JSON_FIELDS)

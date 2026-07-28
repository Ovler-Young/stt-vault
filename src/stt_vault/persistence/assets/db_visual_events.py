from pathlib import Path

from stt_vault.core.api_models import VisualEventResponse
from stt_vault.core.types import PersistedVisualEvent, VisualEvent

from ..shared.db_connection import connect, decode_record, now, transaction


def replace_visual_events(
    db_path: Path,
    asset_id: str,
    events: list[VisualEvent],
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute("DELETE FROM asset_visual_events WHERE asset_id = ?", (asset_id,))
        conn.executemany(
            """
            INSERT INTO asset_visual_events (
                asset_id, event_index, timestamp, score, kind, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    asset_id,
                    index,
                    float(event["timestamp"]),
                    float(event["score"]),
                    event.get("kind", "slide_change"),
                    timestamp,
                )
                for index, event in enumerate(events)
            ],
        )


def list_visual_events(db_path: Path, asset_id: str) -> list[PersistedVisualEvent]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT event_index, timestamp, score, kind, created_at
            FROM asset_visual_events
            WHERE asset_id = ?
            ORDER BY event_index ASC
            """,
            (asset_id,),
        ).fetchall()
    events: list[PersistedVisualEvent] = []
    for row in rows:
        data = decode_record(row)
        if data is None:
            raise RuntimeError("visual event row was not found")
        event = VisualEventResponse.model_validate(data)
        events.append(
            {
                "event_index": event.event_index,
                "timestamp": event.timestamp,
                "score": event.score,
                "kind": event.kind,
                "created_at": event.created_at,
            }
        )
    return events

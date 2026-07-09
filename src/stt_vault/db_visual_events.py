from pathlib import Path
from typing import Any

from .db_connection import connect, now, transaction


def replace_visual_events(
    db_path: Path,
    asset_id: str,
    events: list[dict[str, Any]],
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


def list_visual_events(db_path: Path, asset_id: str) -> list[dict[str, Any]]:
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
    return [dict(row) for row in rows]

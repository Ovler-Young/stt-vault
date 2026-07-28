from pathlib import Path

from ..shared.db_connection import now, transaction


def update_asset_summary(
    db_path: Path,
    asset_id: str,
    *,
    status: str,
    text: str | None = None,
    error: str | None = None,
    model: str | None = None,
    title: str | None = None,
) -> None:
    timestamp = now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE assets
            SET summary_status = ?, summary_text = ?, summary_error = ?, summary_model = ?,
                title = COALESCE(?, title), summary_updated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, text, error, model, title, timestamp, timestamp, asset_id),
        )

import ast
from pathlib import Path

import pytest

from stt_vault.persistence.assets.db_asset_cleanup import (
    delete_asset_with_cleanup_task,
    get_cleanup_task,
    record_cleanup_task,
)
from stt_vault.persistence.assets.db_asset_metadata import (
    update_asset_exports,
    update_diarization_metadata,
)
from stt_vault.persistence.assets.db_asset_records import create_asset, get_asset, list_assets
from stt_vault.persistence.assets.db_asset_relocation import AssetNotFoundError
from stt_vault.persistence.assets.db_asset_retry import retry_asset
from stt_vault.persistence.assets.db_asset_summary import update_asset_summary
from stt_vault.persistence.assets.db_transcripts import (
    apply_ai_speaker_names,
    upsert_transcript_chunk,
)
from stt_vault.persistence.jobs.db_job_events import list_events
from stt_vault.persistence.jobs.db_job_queue import claim_next_job
from stt_vault.persistence.shared.db_schema import initialize


def resolve_import_from_module(module_path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module

    source_path = Path(__file__).parents[1] / "src"
    package_parts = module_path.relative_to(source_path).with_suffix("").parts[:-1]
    parent_package_parts = package_parts[: len(package_parts) - node.level + 1]
    module_parts = node.module.split(".") if node.module else []
    return ".".join((*parent_package_parts, *module_parts))


def is_processing_import(module_path: Path, node: ast.ImportFrom) -> bool:
    resolved_module = resolve_import_from_module(module_path, node)
    if resolved_module is None:
        return False
    if resolved_module == "stt_vault.processing" or resolved_module.startswith(
        "stt_vault.processing."
    ):
        return True
    return node.module is None and any(
        f"{resolved_module}.{imported.name}" == "stt_vault.processing"
        or f"{resolved_module}.{imported.name}".startswith("stt_vault.processing.")
        for imported in node.names
    )


def test_persistence_modules_do_not_import_processing() -> None:
    persistence_path = Path(__file__).parents[1] / "src" / "stt_vault" / "persistence"
    forbidden_imports = []

    for module_path in persistence_path.rglob("*.py"):
        tree = ast.parse(module_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and is_processing_import(module_path, node):
                forbidden_imports.append(f"{module_path}:{node.lineno}: {node.module or ''}")
            if isinstance(node, ast.Import) and any(
                imported.name.startswith("stt_vault.processing") for imported in node.names
            ):
                forbidden_imports.append(f"{module_path}:{node.lineno}")

    assert not forbidden_imports, "\n".join(forbidden_imports)


def test_persistence_boundary_rejects_relative_processing_import() -> None:
    module_path = Path(__file__).parents[1] / "src" / "stt_vault" / "persistence" / "example.py"
    [relative_node] = ast.parse("from ..processing.ai_content import parse_speakers").body
    [absolute_node] = ast.parse("from stt_vault.processing.ai_content import parse_speakers").body
    [package_node] = ast.parse("from .. import processing").body

    assert isinstance(relative_node, ast.ImportFrom)
    assert isinstance(absolute_node, ast.ImportFrom)
    assert isinstance(package_node, ast.ImportFrom)
    assert is_processing_import(module_path, relative_node)
    assert is_processing_import(module_path, absolute_node)
    assert is_processing_import(module_path, package_node)


def initialized_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "stt.sqlite3"
    initialize(db_path)
    return db_path


def test_asset_records_boundary_creates_and_lists_timestamped_assets(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    create_asset(db_path, "asset-1", "2026-07-15_12-57-52.mp4", "video", tmp_path / "clip.mp4")

    [asset] = list_assets(db_path)

    assert asset["id"] == "asset-1"
    assert asset["recorded_at"] == 1_784_120_272
    assert get_asset(db_path, "asset-1", include_event_history=False) is not None


def test_asset_metadata_boundary_persists_diarization_and_exports(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")

    update_diarization_metadata(
        db_path,
        "asset-1",
        wav_path=tmp_path / "clip.wav",
        duration=12.5,
        diarization_stats={"segments": 1},
        raw_segments=[{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
        merged_segments=[{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
        speaker_centroids={"SPEAKER_00": [0.2]},
    )
    update_asset_exports(db_path, "asset-1", {"txt": "/tmp/clip.txt"})

    asset = get_asset(db_path, "asset-1")

    assert asset is not None
    assert asset["diarization_stats"] == {"segments": 1}
    assert asset["exports"] == {"txt": "/tmp/clip.txt"}


def test_asset_summary_boundary_updates_only_summary_fields(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")

    update_asset_summary(db_path, "asset-1", status="success", text="Summary", model="test")

    asset = get_asset(db_path, "asset-1")
    assert asset is not None
    assert asset["summary_text"] == "Summary"


def test_transcript_speaker_name_boundary_updates_unassigned_speakers(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")
    upsert_transcript_chunk(
        db_path,
        "asset-1",
        0,
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "Hello"},
        attempts=1,
    )

    applied = apply_ai_speaker_names(db_path, "asset-1", {"SPEAKER_00": "Maya Chen"})

    asset = get_asset(db_path, "asset-1")
    assert applied == {"SPEAKER_00": "Maya Chen"}
    assert asset is not None
    assert asset["summary_text"] is None
    assert asset["transcript_segments"][0]["speaker_name"] == "Maya Chen"


def test_summary_and_transcript_speaker_name_owners_are_separate() -> None:
    from stt_vault.persistence.assets import db_asset_summary, db_transcripts

    assert not hasattr(db_asset_summary, "apply_ai_speaker_names")
    assert not hasattr(db_transcripts, "update_asset_summary")


def test_asset_retry_boundary_preserves_retry_event_history(tmp_path: Path) -> None:
    db_path = initialized_db(tmp_path)
    create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")
    assert claim_next_job(db_path) == "asset-1"

    retry_asset(db_path, "asset-1")

    [event] = list_events(db_path, "asset-1")
    assert event.message == "Job queued for retry"
    assert event.run_attempt == 2


def test_asset_cleanup_boundary_is_atomic_and_raises_the_shared_not_found_error(
    tmp_path: Path,
) -> None:
    db_path = initialized_db(tmp_path)
    create_asset(db_path, "asset-1", "clip.wav", "audio", tmp_path / "clip.wav")
    record_cleanup_task(db_path, "asset-1", tmp_path / "media", tmp_path / "exports")

    assert get_cleanup_task(db_path, "asset-1") is not None
    delete_asset_with_cleanup_task(db_path, "asset-1", tmp_path / "media", tmp_path / "exports")

    assert get_asset(db_path, "asset-1") is None
    assert get_cleanup_task(db_path, "asset-1") is not None
    with pytest.raises(AssetNotFoundError):
        delete_asset_with_cleanup_task(db_path, "missing", tmp_path / "media", tmp_path / "exports")

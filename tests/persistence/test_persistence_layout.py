import ast
import re
from pathlib import Path

PERSISTENCE_ROOT = Path(__file__).parents[2] / "src" / "stt_vault" / "persistence"
SOURCE_ROOT = Path(__file__).parents[2] / "src"
REPOSITORY_ROOT = Path(__file__).parents[2]

EXPECTED_MODULES = {
    "__init__.py",
    "db.py",
    "assets/__init__.py",
    "assets/db_asset_cleanup.py",
    "assets/db_asset_metadata.py",
    "assets/db_asset_records.py",
    "assets/db_asset_relocation.py",
    "assets/db_asset_retry.py",
    "assets/db_asset_summary.py",
    "assets/db_speaker_assignments.py",
    "assets/db_speakers.py",
    "assets/db_transcripts.py",
    "assets/db_visual_events.py",
    "folders/__init__.py",
    "folders/db_folders.py",
    "folders/folder_records.py",
    "folders/folder_tree.py",
    "jobs/__init__.py",
    "jobs/db_job_events.py",
    "jobs/db_job_queue.py",
    "jobs/db_job_records.py",
    "jobs/db_job_status.py",
    "shared/__init__.py",
    "shared/db_connection.py",
    "shared/db_schema.py",
    "workspace/__init__.py",
    "workspace/db_uploads.py",
    "workspace/worker_repository.py",
}


def test_persistence_modules_have_domain_owned_locations() -> None:
    modules = {
        path.relative_to(PERSISTENCE_ROOT).as_posix() for path in PERSISTENCE_ROOT.rglob("*.py")
    }

    assert modules == EXPECTED_MODULES
    assert {path.name for path in PERSISTENCE_ROOT.glob("*.py")} == {"__init__.py", "db.py"}
    for package in ("assets", "folders", "jobs", "shared", "workspace"):
        assert (PERSISTENCE_ROOT / package / "__init__.py").read_text() == ""


def test_source_and_tests_do_not_import_retired_persistence_module_paths() -> None:
    retired_modules = {
        path.stem
        for path in PERSISTENCE_ROOT.rglob("*.py")
        if path.parent != PERSISTENCE_ROOT and path.name != "__init__.py"
    }
    stale_imports: list[str] = []

    for source_path in (
        *SOURCE_ROOT.rglob("*.py"),
        *(REPOSITORY_ROOT / "tests").rglob("*.py"),
    ):
        tree = ast.parse(source_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("stt_vault.persistence."):
                    module_name = node.module.rsplit(".", maxsplit=1)[-1]
                    if module_name in retired_modules and node.module.count(".") == 2:
                        stale_imports.append(f"{source_path}:{node.lineno}: {node.module}")
                if node.module == "stt_vault.persistence":
                    stale_imports.extend(
                        f"{source_path}:{node.lineno}: {imported.name}"
                        for imported in node.names
                        if imported.name in retired_modules
                    )

    assert not stale_imports, "\n".join(stale_imports)


def test_source_and_tests_do_not_reference_retired_persistence_module_paths() -> None:
    retired_modules = "|".join(
        sorted(
            path.stem
            for path in PERSISTENCE_ROOT.rglob("*.py")
            if path.parent != PERSISTENCE_ROOT and path.name != "__init__.py"
        )
    )
    retired_path = re.compile(rf"stt_vault\.persistence\.({retired_modules})(?:\.|\b)")
    stale_references = [
        f"{source_path}:{match.group(0)}"
        for source_path in (
            *SOURCE_ROOT.rglob("*.py"),
            *(REPOSITORY_ROOT / "tests").rglob("*.py"),
        )
        for match in retired_path.finditer(source_path.read_text())
    ]

    assert not stale_references, "\n".join(stale_references)

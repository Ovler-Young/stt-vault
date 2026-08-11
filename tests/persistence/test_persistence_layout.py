import ast
import re
from pathlib import Path

PERSISTENCE_ROOT = Path(__file__).parents[2] / "src" / "stt_vault" / "persistence"
SOURCE_ROOT = Path(__file__).parents[2] / "src"
REPOSITORY_ROOT = Path(__file__).parents[2]

EXPECTED_MODULES = {"__init__.py", "sqlite_database.py"}


def test_persistence_has_one_sqlite_implementation() -> None:
    modules = {
        path.relative_to(PERSISTENCE_ROOT).as_posix() for path in PERSISTENCE_ROOT.rglob("*.py")
    }

    assert modules == EXPECTED_MODULES
    assert {path.name for path in PERSISTENCE_ROOT.glob("*.py")} == EXPECTED_MODULES


def test_source_and_tests_only_import_the_sqlite_database_boundary() -> None:
    stale_imports: list[str] = []

    for source_path in (
        *SOURCE_ROOT.rglob("*.py"),
        *(REPOSITORY_ROOT / "tests").rglob("*.py"),
    ):
        tree = ast.parse(source_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("stt_vault.persistence"):
                    if node.module != "stt_vault.persistence.sqlite_database":
                        stale_imports.append(f"{source_path}:{node.lineno}: {node.module}")

    assert not stale_imports, "\n".join(stale_imports)


def test_source_and_tests_do_not_reference_deleted_persistence_modules() -> None:
    retired_path = re.compile(
        r"stt_vault\.persistence\.(?:assets|folders|jobs|shared|workspace|db)(?:\.|\b)"
    )
    stale_references = [
        f"{source_path}:{match.group(0)}"
        for source_path in (
            *SOURCE_ROOT.rglob("*.py"),
            *(REPOSITORY_ROOT / "tests").rglob("*.py"),
        )
        for match in retired_path.finditer(source_path.read_text())
    ]

    assert not stale_references, "\n".join(stale_references)

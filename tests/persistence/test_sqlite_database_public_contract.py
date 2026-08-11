import inspect
from dataclasses import fields, is_dataclass
from pathlib import Path
from threading import Barrier, Thread
from typing import Any, get_args, get_origin, get_type_hints

import pytest

from stt_vault.core.models import persistence_errors, records
from stt_vault.persistence.sqlite_database import SqliteDatabase


def test_persistence_public_records_are_named_immutable_dataclasses() -> None:
    """The database boundary cannot expose mutable dictionary-shaped records."""
    public_record_types = [
        value
        for name, value in vars(records).items()
        if not name.startswith("_") and isinstance(value, type) and is_dataclass(value)
    ]
    assert public_record_types
    assert all(record_type.__dataclass_params__.frozen for record_type in public_record_types)
    forbidden: list[str] = []
    for record_type in public_record_types:
        for field in fields(record_type):
            if _has_mutable_boundary_type(field.type):
                forbidden.append(f"{record_type.__name__}.{field.name}: {field.type}")
    assert not forbidden, f"mutable persistence-record fields: {forbidden}"


def test_sqlite_database_public_method_annotations_expose_no_dictionary_surface() -> None:
    """Commands and results are named records instead of dictionary compatibility APIs."""
    methods = [
        method
        for name, method in inspect.getmembers(SqliteDatabase, inspect.isfunction)
        if not name.startswith("_")
    ]
    assert methods
    forbidden = []
    for method in methods:
        annotations = get_type_hints(method)
        if any("dict" in str(annotation).lower() for annotation in annotations.values()):
            forbidden.append(method.__name__)
    assert not forbidden, f"dictionary-shaped public persistence API: {forbidden}"


def test_diarization_has_a_dedicated_conditional_completion_operation() -> None:
    """Diarization success cannot bypass the provider ledger's conditional transaction."""
    assert callable(getattr(SqliteDatabase, "complete_diarization_and_provider_invocation", None))
    assert hasattr(records, "CompleteDiarizationProviderInvocation")


def test_close_is_idempotent_and_rejects_concurrent_and_later_operations(tmp_path: Path) -> None:
    """Once shutdown starts, no caller may open a new SQLite operation."""
    database = SqliteDatabase(tmp_path / "closed.sqlite3")
    database.initialize()
    database.close()
    database.close()
    error_type = getattr(persistence_errors, "DatabaseClosedError", None)
    assert error_type is not None
    barrier = Barrier(3)
    errors: list[Exception] = []

    def read_after_close() -> None:
        barrier.wait()
        try:
            database.list_assets()
        except Exception as error:
            errors.append(error)

    threads = [Thread(target=read_after_close), Thread(target=read_after_close)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    with pytest.raises(error_type):
        database.initialize()
    with pytest.raises(error_type):
        database.list_assets()
    assert len(errors) == 2
    assert all(isinstance(error, error_type) for error in errors)


def _has_mutable_boundary_type(annotation: object) -> bool:
    origin = get_origin(annotation)
    if annotation is Any or annotation in {dict, list, set}:
        return True
    if origin in {dict, list, set}:
        return True
    return any(_has_mutable_boundary_type(argument) for argument in get_args(annotation))

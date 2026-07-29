from pathlib import Path

PROCESSING_ROOT = Path(__file__).parents[2] / "src" / "stt_vault" / "processing"


def test_processing_direct_source_files_stay_below_package_threshold() -> None:
    direct_source_files = {
        path.name for path in PROCESSING_ROOT.glob("*.py") if path.name != "__init__.py"
    }

    assert len(direct_source_files) < 15
    assert direct_source_files.isdisjoint(
        {
            "diarization_contracts.py",
            "diarization_instrumentation.py",
            "diarization_pipeline.py",
            "senko_diarization.py",
        }
    )


def test_diarization_package_owns_its_cohesive_modules() -> None:
    diarization_modules = {path.name for path in (PROCESSING_ROOT / "diarization").glob("*.py")}

    assert diarization_modules == {
        "__init__.py",
        "contracts.py",
        "instrumentation.py",
        "pipeline.py",
        "senko.py",
    }

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_timed_transcript_fixture_matches_the_production_provider_hostname() -> None:
    compose = (REPOSITORY_ROOT / "docker-compose.e2e-timed-transcript.yml").read_text()

    assert "mod-whisper-cpu:" in compose
    assert "STT_TRANSCRIPTION_PROVIDER=mod-whisper-cpu" in compose


def test_timed_transcript_fixture_disables_automatic_summary_generation() -> None:
    compose = (REPOSITORY_ROOT / "docker-compose.e2e-timed-transcript.yml").read_text()

    assert "STT_AUTO_SUMMARY_ENABLED=false" in compose


def test_timed_transcript_global_setup_isolates_and_captures_each_run() -> None:
    setup = (REPOSITORY_ROOT / "web/e2e/timed-transcript.global.ts").read_text()

    assert '"compose", "-p", run.projectName' in setup
    assert "STT_HOST_DATA_DIR" in setup
    assert "assertManagedProject" in setup
    assert "assertManagedTemporaryDirectory" in setup
    assert "mkdtemp(join(tmpdir(), projectPrefix))" in setup
    assert "timed-transcript-compose.log" in setup
    assert setup.index('compose(run, ["logs", "--no-color"])') < setup.index(
        'compose(run, ["down", "--volumes", "--remove-orphans"])'
    )


def test_ci_uploads_the_artifacts_captured_before_compose_cleanup() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/docker.yml").read_text()

    assert "web/test-results/timed-transcript-compose.log" in workflow
    assert "web/test-results" in workflow
    assert "web/playwright-report" in workflow

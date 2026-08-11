from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_timed_transcript_fixture_matches_the_production_provider_hostname() -> None:
    compose = (REPOSITORY_ROOT / "docker-compose.e2e-timed-transcript.yml").read_text()

    assert "mod-whisper-cpu:" in compose
    assert "STT_TRANSCRIPTION_PROVIDER=mod-whisper-cpu" in compose


def test_timed_transcript_fixture_explicitly_publishes_the_playwright_port() -> None:
    compose = (REPOSITORY_ROOT / "docker-compose.e2e-timed-transcript.yml").read_text()

    assert '"${APP_PORT:-18080}:8080"' in compose
    assert "timed-transcript-public" in compose


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
    assert setup.index('captureComposeDiagnostic(run, ["logs", "--no-color"])') < setup.index(
        'compose(run, ["down", "--volumes", "--remove-orphans"])'
    )


def test_timed_transcript_global_setup_waits_for_public_health_and_auth() -> None:
    setup = (REPOSITORY_ROOT / "web/e2e/timed-transcript.global.ts").read_text()

    assert "waitForPublicAppReadiness" in setup
    assert "/api/health" in setup
    assert "/api/auth/token" in setup
    assert '"ps", "--format", "json"' in setup


def test_timed_transcript_global_setup_bounds_compose_health_wait() -> None:
    setup = (REPOSITORY_ROOT / "web/e2e/timed-transcript.global.ts").read_text()

    assert '"--wait-timeout"' in setup
    assert "String(composeWaitTimeoutSeconds)" in setup
    assert "composeWaitTimeoutSeconds" in setup
    assert setup.index('captureComposeDiagnostic(run, ["ps", "--format", "json"])') < setup.index(
        'compose(run, ["down", "--volumes", "--remove-orphans"])'
    )


def test_ci_uploads_the_artifacts_captured_before_compose_cleanup() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/docker.yml").read_text()

    assert "web/test-results/timed-transcript-compose.log" in workflow
    assert "web/test-results" in workflow
    assert "web/playwright-report" in workflow

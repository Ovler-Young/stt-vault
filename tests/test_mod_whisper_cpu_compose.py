import ast
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docker-compose.yml"
SHARED = ROOT / "docker-compose.mods.yml"
WHISPER = ROOT / "docker-compose.mod-whisper-cpu.yml"
MANIFEST = ROOT / "mods/mod-whisper-cpu/model-manifest.json"
GATE = ROOT / "mods/gates/mod-whisper-cpu/0.1.0.json"
RELEASE = ROOT / "mods/releases/mod-whisper-cpu/0.1.0.json"
WORKFLOW = ROOT / ".github/workflows/docker.yml"
TAG_TRUST_POLICY = ROOT / ".github/trust/release-tag-signers.v1.json"
TAG_TRUST_README = ROOT / ".github/trust/README.md"
SMOKE_SCRIPT = ROOT / "tests/e2e/fixtures/mod_whisper_cpu_smoke.py"
APP_SMOKE_SCRIPT = ROOT / "tests/e2e/fixtures/app_mod_whisper_cpu_smoke.py"
CI_COMPOSE = ROOT / "tests/e2e/fixtures/docker-compose.mod-whisper-cpu-ci.yml"
DOCKERFILE = ROOT / "mods/mod-whisper-cpu/Dockerfile"
CORE_CONTRACT = ROOT / "src/stt_vault/core/models/mod_contracts.py"
MOD_CONTRACT_WRAPPER = ROOT / "mods/mod-whisper-cpu/src/mod_whisper_cpu/contracts.py"
MOD_CONTRACT_ARTIFACT = ROOT / "mods/mod-whisper-cpu/src/mod_whisper_cpu/contract_v1_artifact.py"
TRIVY_ACTION_TAG = "v0.36.0"
TRIVY_ACTION_SHA = "ed142fd0673e97e23eac54620cfb913e5ce36c25"


def _trivy_action_pin(workflow: str) -> tuple[str, str]:
    match = re.search(
        r"^\s*uses:\s+aquasecurity/trivy-action@(?P<sha>[^\s#]+)\s+#\s+(?P<tag>\S+)\s*$",
        workflow,
        flags=re.MULTILINE,
    )
    assert match is not None, "Trivy must use a full SHA pin with a version comment"

    sha = match["sha"]
    tag = match["tag"]
    assert re.fullmatch(r"[a-f0-9]{40}", sha), "Trivy action pin must be a full SHA"
    assert tag == TRIVY_ACTION_TAG
    assert sha == TRIVY_ACTION_SHA
    return tag, sha


def _compose(path: Path) -> dict[str, object]:
    assert path.is_file(), f"missing required Compose file: {path.name}"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_whisper_cpu_compose_uses_one_internal_selected_sidecar() -> None:
    base = _compose(BASE)
    shared = _compose(SHARED)
    whisper = _compose(WHISPER)

    assert "services" not in shared
    assert shared["networks"] == {"stt-mods": {"internal": True}}
    assert "stt_mod_token" in shared["secrets"]
    assert shared["secrets"]["stt_mod_token"] == {
        "file": "${STT_MOD_TOKEN_FILE:?STT_MOD_TOKEN_FILE is required}"
    }

    services = whisper["services"]
    assert set(services) == {"stt-vault", "mod-whisper-cpu"}
    app = services["stt-vault"]
    mod = services["mod-whisper-cpu"]
    assert app["depends_on"] == {"mod-whisper-cpu": {"condition": "service_healthy"}}
    assert mod["profiles"] == ["mod-whisper-cpu"]
    assert mod["image"] == "${STT_MOD_WHISPER_CPU_IMAGE:?}@${STT_MOD_WHISPER_CPU_DIGEST:?}"
    assert mod["environment"] == {
        "WHISPER_MODEL_ID": "${STT_MOD_WHISPER_CPU_MODEL:-ggml-base.en.bin}",
        "WHISPER_IMAGE_DIGEST": "${STT_MOD_WHISPER_CPU_DIGEST:?}",
    }
    assert "ports" not in mod
    assert mod["networks"] == ["stt-mods"]
    assert "stt-mods" in app["networks"]
    assert "default" in app["networks"]
    assert app["environment"] == {
        "STT_TRANSCRIPTION_PROVIDER": "mod-whisper-cpu",
        "STT_DIARIZATION_PROVIDER": "senko",
        "STT_MOD_WHISPER_CPU_DIGEST": "${STT_MOD_WHISPER_CPU_DIGEST:?}",
    }
    assert "stt_mod_token" in app["secrets"]
    assert "stt_mod_token" in mod["secrets"]
    assert set(base["services"]) == {"stt-vault"}
    assert "networks" not in base


def test_whisper_cpu_compose_has_required_cpu_resources_secret_healthcheck_and_isolation() -> None:
    whisper = _compose(WHISPER)
    mod = whisper["services"]["mod-whisper-cpu"]

    assert mod["cpus"] == "2.0"
    assert mod["mem_limit"] == "4g"
    assert mod["pids_limit"] == 256
    assert mod["tmpfs"] == ["/tmp:size=512m,noexec,nosuid"]
    assert mod["volumes"] == ["mod_whisper_cpu_cache:/models"]
    assert "gpus" not in mod
    assert "deploy" not in mod
    assert whisper["volumes"] == {"mod_whisper_cpu_cache": {}}
    assert whisper["services"]["stt-vault"]["secrets"] == {
        "stt_mod_token": {"uid": "0", "gid": "0", "mode": 0o400}
    }
    assert mod["secrets"] == {"stt_mod_token": {"uid": "10001", "gid": "10001", "mode": 0o400}}

    healthcheck = mod["healthcheck"]
    command = " ".join(str(part) for part in healthcheck["test"])
    assert "/run/secrets/stt_mod_token" in command
    assert "Authorization: Bearer" in command
    assert "http://127.0.0.1:8081/readyz" in command
    assert healthcheck["interval"] == "10s"
    assert healthcheck["timeout"] == "5s"
    assert healthcheck["retries"] == 3
    assert healthcheck["start_period"] == "30s"

    serialized = str(mod)
    for forbidden in ("/data", "app.sqlite3", "/app/.env", "STT_HOST_DATA_DIR", "uploads"):
        assert forbidden not in serialized


def test_whisper_cpu_compose_selects_only_models_declared_by_the_manifest() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert set(manifest["models"]) == {
        "ggml-tiny.en.bin",
        "ggml-base.en.bin",
        "ggml-small.en.bin",
        "ggml-medium.en.bin",
        "ggml-large-v3.bin",
        "ggml-large-v3-turbo.bin",
    }
    for model_id, model in manifest["models"].items():
        assert model["id"] == model_id
        assert re.fullmatch(r"[a-f0-9]{64}", model["sha256"])
        assert model["revision"] == manifest["source"]["revision"]
        assert model["access_declaration"] == "public"
        assert model["license_ref"].startswith("https://huggingface.co/")

    whisper = _compose(WHISPER)
    assert whisper["services"]["mod-whisper-cpu"]["environment"]["WHISPER_MODEL_ID"] == (
        "${STT_MOD_WHISPER_CPU_MODEL:-ggml-base.en.bin}"
    )


def test_whisper_cpu_gate_and_release_records_match_the_manifest() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    gate = json.loads(GATE.read_text(encoding="utf-8"))
    release = json.loads(RELEASE.read_text(encoding="utf-8"))

    assert gate["service"] == release["service"] == "mod-whisper-cpu"
    assert gate["version"] == release["version"] == "0.1.0"
    assert gate["contract_version"] == release["contract_version"] == "v1"
    assert gate["runtime"] == {"device": "cpu", "cuda": None, "minimum_host_driver": None}
    assert gate["model"]["id"] in manifest["models"]
    assert gate["model"]["sha256"] == manifest["models"][gate["model"]["id"]]["sha256"]
    assert gate["model"]["revision"] == manifest["source"]["revision"]
    assert gate["model"]["license_url"].startswith("https://huggingface.co/")
    assert gate["status"] == "blocked-pending-ci-evidence"
    assert gate["fixture"] == {
        "id": "transcription-v1-001",
        "model_id": "ggml-base.en.bin",
        "sha256": "2976da01e205a110c9fa41d47659e238a5c6d3c3f3137582f2949853faa201dd",
        "expected_result": "no_speech",
        "sample_rate_hz": 16000,
        "channels": 1,
        "segment_tolerance_ms": 50,
        "evidence": "pending-ci: mod-whisper-cpu-smoke",
    }
    assert gate["required_ci_gates"] == [
        "mod-whisper-cpu-smoke",
        "mod-whisper-cpu-release-evidence",
    ]
    assert gate["approver"] is None
    assert gate["approval_date"] is None
    assert release["model_manifest"] == "mods/mod-whisper-cpu/model-manifest.json"
    assert release["gate_record"] == "mods/gates/mod-whisper-cpu/0.1.0.json"
    assert release["status"] == "blocked-pending-ci-evidence"
    assert release["fixture_result"] is None
    assert release["signature"] == {
        "status": "required-ci",
        "required_gate": "mod-whisper-cpu-release-evidence",
    }
    assert release["provenance_attestation"] == {
        "status": "required-ci",
        "required_gate": "mod-whisper-cpu-release-evidence",
    }
    assert release["image_digest"] is None
    assert release["sbom_digest"] is None


def test_release_record_rejects_zero_or_incomplete_published_evidence() -> None:
    release = json.loads(RELEASE.read_text(encoding="utf-8"))
    assert release["status"] != "released"
    for field in ("image_digest", "sbom_digest", "fixture_result"):
        assert release[field] is None or release[field] != "sha256:" + "0" * 64


def test_release_gate_requires_base_model_evidence() -> None:
    gate = json.loads(GATE.read_text(encoding="utf-8"))

    assert gate["fixture"]["model_id"] == gate["model"]["id"]
    assert gate["fixture"]["evidence"] == "pending-ci: mod-whisper-cpu-smoke"


def test_whisper_cpu_ci_smoke_is_filtered_blocking_and_cpu_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "mod_whisper_cpu: ${{ steps.filter.outputs.mod_whisper_cpu }}" in workflow
    for path in (
        "mods/mod-whisper-cpu/**",
        "mods/gates/mod-whisper-cpu/**",
        "mods/releases/mod-whisper-cpu/**",
        "docker-compose.mods.yml",
        "docker-compose.mod-whisper-cpu.yml",
        "tests/test_mod_whisper_cpu_compose.py",
    ):
        assert path in workflow
    assert "mod-whisper-cpu-smoke:" in workflow
    assert "needs.changes.outputs.mod_whisper_cpu == 'true'" in workflow
    assert "uv run --extra dev pytest -q tests/test_mod_whisper_cpu_compose.py" in workflow
    assert "Select PR tiny or tag base smoke candidate" in workflow
    assert "STT_MOD_WHISPER_CPU_MODEL=$model" in workflow
    assert "build-args: WHISPER_MODEL_ID=${{ env.STT_MOD_WHISPER_CPU_MODEL }}" in workflow
    assert "steps.build_mod.outputs.digest" in workflow
    assert 'echo "STT_MOD_WHISPER_CPU_DIGEST=$digest" >> "$GITHUB_ENV"' in workflow
    assert 'docker image inspect "$STT_MOD_WHISPER_CPU_IMAGE"' in workflow
    assert '"candidate_image_id"' in workflow
    assert '"source_commit"' in workflow
    assert '"tag_object"' in workflow
    assert '"image_revision"' in workflow
    assert ".[0].Image == $selected_image_id" in workflow
    assert '.[0].Config.Env | index("WHISPER_MODEL_ID=" + $selected_model)' in workflow
    assert "docker compose" in workflow
    assert "Render fallback service set" in workflow
    assert "ps --services --status running" in workflow
    assert "mod_whisper_cpu_smoke.py" in workflow
    assert "docker inspect" in workflow
    assert "all(.[0].Mounts[]" in workflow
    assert "restart mod-whisper-cpu" in workflow
    assert "anchore/sbom-action@" in workflow
    assert _trivy_action_pin(workflow) == (TRIVY_ACTION_TAG, TRIVY_ACTION_SHA)
    assert "format: sarif" in workflow
    assert "output: mod-whisper-cpu.trivy.sarif" in workflow
    assert "retention-days: 30" in workflow
    assert "ignore-unfixed: false" in workflow
    assert "limit-severities-for-sarif: true" in workflow
    assert "severity: HIGH,CRITICAL" in workflow
    assert 'exit-code: "1"' in workflow
    assert "mod-whisper-cpu-release-evidence:" in workflow
    assert "needs: mod-whisper-cpu-smoke" in workflow
    assert "Publish the selected release image" not in workflow
    assert "id-token: write" in workflow
    assert "attestations: write" in workflow
    assert "push: ${{ github.event_name == 'push'" in workflow
    assert "continue-on-error" not in workflow
    assert "down --volumes --remove-orphans" in workflow
    assert "if: always()" in workflow
    assert "app_mod_whisper_cpu_smoke.py" in workflow
    assert "jfk.wav" in workflow
    assert "59dfb9a4acb36fe2a2affc14bacbee2920ff435cb13cc314a08c13f66ba7860e" in workflow
    assert "cosign sign --yes" in workflow
    assert "actions/attest-build-provenance@v2" in workflow
    assert "gh release upload" in workflow
    assert "MOD_WHISPER_CPU_RELEASE_ENABLED" in workflow
    assert "COSIGN_PRIVATE_KEY" in workflow
    assert "COSIGN_PASSWORD" in workflow
    assert "release-evidence.json" in workflow
    assert 'test "$MOD_WHISPER_CPU_RELEASE_ENABLED" = "true"' in workflow
    assert 'test -n "$COSIGN_PRIVATE_KEY"' in workflow
    assert 'test -n "$COSIGN_PASSWORD"' in workflow
    assert '"status":"released"' in workflow
    assert '"image_digest":"%s"' in workflow
    assert '"sbom_digest":"%s"' in workflow
    assert '"model_id":"%s"' in workflow
    assert '"source_artifact":"mod-whisper-cpu-smoke-artifacts"' in workflow
    assert "selected-supply-chain.json" in workflow
    assert ".image_digest == $digest" in workflow


def test_whisper_cpu_ci_rejects_mutable_or_incomplete_trivy_action_pins() -> None:
    for pin in (
        "ed142fd0673e97e23eac54620cfb913e5ce36c25",
        "v0.36.0 # v0.36.0",
        "ed142fd0673e97e23eac54620cfb913e5ce36c # v0.36.0",
    ):
        workflow = f"uses: aquasecurity/trivy-action@{pin}\n"
        with pytest.raises(AssertionError):
            _trivy_action_pin(workflow)


def test_whisper_cpu_ci_uses_one_parameterized_compose_smoke_for_pr_and_tag() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    smoke_job = workflow.split("  mod-whisper-cpu-smoke:", 1)[1].split(
        "  mod-whisper-cpu-release-evidence:", 1
    )[0]

    assert "ggml-tiny.en.bin" in smoke_job
    assert "ggml-base.en.bin" in smoke_job
    assert "push: false" in smoke_job
    assert "load: true" in smoke_job
    assert 'docker pull "$STT_MOD_WHISPER_CPU_IMAGE@$digest"' not in smoke_job
    assert "app_mod_whisper_cpu_smoke.py" in smoke_job
    assert smoke_job.count("Start the selected Compose smoke") == 1
    assert "mod-whisper-cpu-release.spdx.json" not in smoke_job

    release_job = workflow.split("  mod-whisper-cpu-release-evidence:", 1)[1].split("  build:", 1)[
        0
    ]
    assert "ggml-tiny.en.bin" not in release_job
    assert '.model == "ggml-base.en.bin"' in release_job
    assert '.model_id == "ggml-base.en.bin"' in release_job
    assert "image_digest == $digest" in release_job
    assert "Stage the verified Mod image" in release_job
    assert "Verify the staged Mod signature" in release_job
    assert "Verify the staged Mod provenance" in release_job
    assert "Promote the verified Mod image to its canonical tag" in release_job


def test_whisper_cpu_tag_release_promotes_only_the_verified_canonical_digest() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    smoke_job = workflow.split("  mod-whisper-cpu-smoke:", 1)[1].split(
        "  mod-whisper-cpu-release-evidence:", 1
    )[0]
    release_job = workflow.split("  mod-whisper-cpu-release-evidence:", 1)[1].split("  build:", 1)[
        0
    ]
    canonical_image = "ghcr.io/ovler-young/stt-vault-mod-mod-whisper-cpu:mod-whisper-cpu-0.1.0-cpu"

    assert canonical_image in workflow
    assert "GITHUB_REF_NAME" not in smoke_job
    assert "GITHUB_REF_NAME" not in release_job
    assert "push: false" in smoke_job
    assert "load: true" in smoke_job
    assert "push: true" not in smoke_job

    required_gate = release_job.index("Require enabled credential-backed base candidate evidence")
    stage = release_job.index("Stage the verified Mod image")
    sign = release_job.index("Sign the staged Mod image")
    attest = release_job.index("Attest staged image provenance")
    verify_signature = release_job.index("Verify the staged Mod signature")
    verify_provenance = release_job.index("Verify the staged Mod provenance")
    promote = release_job.index("Promote the verified Mod image to its canonical tag")
    assert required_gate < stage < sign < attest < verify_signature < verify_provenance < promote
    assert "docker buildx build" not in release_job
    promotion_command = (
        'docker buildx imagetools create --tag "$RELEASE_IMAGE" '
        '"$STAGED_IMAGE@$RELEASE_IMAGE_DIGEST"'
    )
    assert promotion_command in release_job
    assert (
        'cosign sign --yes --key env://COSIGN_PRIVATE_KEY "$STAGED_IMAGE@$RELEASE_IMAGE_DIGEST"'
        in release_job
    )
    assert "subject-name: ${{ env.STAGED_IMAGE }}" in release_job
    assert "subject-digest: ${{ env.RELEASE_IMAGE_DIGEST }}" in release_job
    assert (
        'cosign verify --key env://COSIGN_PUBLIC_KEY "$STAGED_IMAGE@$RELEASE_IMAGE_DIGEST"'
        in release_job
    )
    assert (
        'gh attestation verify "oci://$STAGED_IMAGE@$RELEASE_IMAGE_DIGEST" '
        '--repo "$GITHUB_REPOSITORY" --predicate-type "https://slsa.dev/provenance/v1"'
        in release_job
    )
    assert "cosign verify-attestation" not in release_job
    assert "candidate_image_id" in release_job
    assert 'test "$digest" = "$candidate_digest"' in release_job
    assert 'model_id == "ggml-base.en.bin"' in release_job
    assert "sbom_digest" in release_job
    assert "scan_digest" in release_job
    assert "license_digest" in release_job
    assert "fixture_result" in release_job
    assert "Cleanup failed staged Mod image" in release_job
    assert "if: failure()" in release_job

    ci_compose = _compose(CI_COMPOSE)
    assert ci_compose["services"]["mod-whisper-cpu"]["image"] == (
        "${STT_MOD_WHISPER_CPU_IMAGE:?}@${STT_MOD_WHISPER_CPU_DIGEST:?}"
    )


def test_whisper_cpu_release_requires_a_trusted_annotated_tag_before_registry_staging() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    release_job = workflow.split("  mod-whisper-cpu-release-evidence:", 1)[1].split("  build:", 1)[
        0
    ]

    verify_tag = release_job.index("Verify the signed annotated release tag")
    login = release_job.index("Log in to GitHub Container Registry for gated staging")
    stage = release_job.index("Stage the verified Mod image")
    assert verify_tag < login < stage
    for required in (
        "fetch-depth: 0",
        "git fetch --no-tags origin "
        '"refs/tags/$MOD_WHISPER_CPU_GIT_TAG:refs/stt-vault/verified-release-tag"',
        'git cat-file -e "$verified_tag_ref^{tag}"',
        "GNUPGHOME=",
        "release-tag-signers.v1.json",
        "active_signers",
        "VALIDSIG",
        "EXPKEYSIG",
        "REVKEYSIG",
        "tag_object",
        "peeled_commit",
        "signer_fingerprint",
        "trust_policy_digest",
        "refs/stt-vault/verified-release-tag",
        'git verify-tag --raw "$tag_object"',
        'test "$GITHUB_SHA" = "$peeled_commit"',
        'test "$checkout_commit" = "$peeled_commit"',
    ):
        assert required in release_job


def test_release_binds_fetched_tag_to_smoked_source_before_login() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    smoke_job = workflow.split("  mod-whisper-cpu-smoke:", 1)[1].split(
        "  mod-whisper-cpu-release-evidence:", 1
    )[0]
    release_job = workflow.split("  mod-whisper-cpu-release-evidence:", 1)[1].split("  build:", 1)[
        0
    ]

    for required in (
        "labels: org.opencontainers.image.revision=${{ github.sha }}",
        'checkout_commit="$(git rev-parse HEAD)"',
        'test "$checkout_commit" = "$GITHUB_SHA"',
        'image_revision="$(docker image inspect "$STT_MOD_WHISPER_CPU_IMAGE"',
        'test "$image_revision" = "$checkout_commit"',
        '"source_commit":"%s"',
        '"image_revision":"%s"',
    ):
        assert required in smoke_job

    bind_source = release_job.index("Bind the fetched tag object to the smoke candidate source")
    login = release_job.index("Log in to GitHub Container Registry for gated staging")
    stage = release_job.index("Stage the verified Mod image")
    assert bind_source < login < stage
    for required in (
        "smoke_tag_object=\"$(jq -er '.tag_object' ",
        'test "$smoke_tag_object" = "$TAG_OBJECT"',
        'test "$smoke_source_commit" = "$PEELED_COMMIT"',
        'test "$smoke_image_revision" = "$PEELED_COMMIT"',
        'test "$supply_chain_source_commit" = "$PEELED_COMMIT"',
        'test "$supply_chain_image_revision" = "$PEELED_COMMIT"',
        'git rev-parse "$TAG_OBJECT^{commit}"',
    ):
        assert required in release_job


def test_release_records_verified_source_commit_in_evidence() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    release_job = workflow.split("  mod-whisper-cpu-release-evidence:", 1)[1].split("  build:", 1)[
        0
    ]

    candidate = release_job[
        release_job.index("Record candidate evidence before promotion") : release_job.index(
            "Calculate release SBOM digest"
        )
    ]
    released = release_job[
        release_job.index("Record CI-produced release evidence") : release_job.index(
            "Publish release evidence"
        )
    ]
    for evidence in (candidate, released):
        assert '"source_commit":"%s"' in evidence
        assert ".source_commit == $peeled_commit" in evidence


def test_release_verifies_github_attestation_before_promotion() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    release_job = workflow.split("  mod-whisper-cpu-release-evidence:", 1)[1].split("  build:", 1)[
        0
    ]

    verify_signature = release_job.index("Verify the staged Mod signature")
    verify_provenance = release_job.index("Verify the staged Mod provenance")
    promote = release_job.index("Promote the verified Mod image to its canonical tag")
    assert verify_signature < verify_provenance < promote
    assert "COSIGN_PUBLIC_KEY" not in release_job[verify_provenance:promote]
    assert '"oci://$STAGED_IMAGE@$RELEASE_IMAGE_DIGEST"' in release_job[verify_provenance:promote]
    assert '--repo "$GITHUB_REPOSITORY"' in release_job[verify_provenance:promote]
    assert (
        '--predicate-type "https://slsa.dev/provenance/v1"'
        in release_job[verify_provenance:promote]
    )


def test_release_tag_trust_policy_is_public_only_and_fails_closed_without_a_signer() -> None:
    policy = json.loads(TAG_TRUST_POLICY.read_text(encoding="utf-8"))

    assert policy == {
        "schema": "stt-vault.release-tag-signers",
        "schema_version": 1,
        "service": "mod-whisper-cpu",
        "release_tag": {
            "annotated_only": True,
            "prefix": "v",
            "versions": ["0.1.0"],
        },
        "key_directory": "release-tag-keys",
        "active_signers": [],
        "retired_signers": [],
        "rotation": {
            "activation_requires": [
                "public_key",
                "fingerprint",
                "status",
                "not_before",
                "not_after",
            ],
            "retirement_requires": ["fingerprint", "revoked_at_or_expired_at"],
            "private_keys_permitted": False,
        },
    }
    assert "No active signer is committed" in TAG_TRUST_README.read_text(encoding="utf-8")


def test_whisper_cpu_release_emits_released_evidence_only_after_canonical_reinspection() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    release_job = workflow.split("  mod-whisper-cpu-release-evidence:", 1)[1].split("  build:", 1)[
        0
    ]

    promote = release_job.index("Promote the verified Mod image to its canonical tag")
    reinspect = release_job.index("Reinspect the canonical Mod image")
    record = release_job.index("Record CI-produced release evidence")
    publish = release_job.index("Publish release evidence")
    assert promote < reinspect < record < publish
    evidence = release_job[record:publish]
    for required in (
        '"status":"released"',
        '"tag_object":"%s"',
        '"peeled_commit":"%s"',
        '"signer_fingerprint":"%s"',
        '"trust_policy_digest":"%s"',
        '"candidate_image_digest":"%s"',
        '"staged_image_digest":"%s"',
        '"canonical_image_digest":"%s"',
        '"model":%s',
        '"scan_digest":"%s"',
        '"license_digest":"%s"',
        '"source_digest":"%s"',
    ):
        assert required in evidence


def test_app_smoke_uses_production_states_and_checks_the_provider_ledger() -> None:
    smoke = APP_SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert 'IN_PROGRESS_STATES = {"queued", "processing"}' in smoke
    assert 'SUCCESS_STATE = "success"' in smoke
    assert 'job.get("status") in {"completed", "failed"}' not in smoke
    assert 'detail.get("status") == "completed"' not in smoke
    assert "terminal failure or inconsistent asset/job state" in smoke
    assert 'sqlite3.connect("/data/app.sqlite3")' in smoke
    assert "provider_work_items" in smoke
    assert "work_item.provider_id = ?" in smoke
    assert "work_item.image_digest = ?" in smoke
    assert "EXPECTED_PROVIDER_ID" in smoke
    assert "EXPECTED_IMAGE_DIGEST" in smoke
    assert "app-smoke-evidence.json" in smoke


def test_whisper_cpu_smoke_fixture_is_deterministic_and_checks_the_release_signals() -> None:
    smoke = SMOKE_SCRIPT.read_text(encoding="utf-8")

    fixture_sha256 = "2976da01e205a110c9fa41d47659e238a5c6d3c3f3137582f2949853faa201dd"
    assert f'FIXTURE_SHA256 = "{fixture_sha256}"' in smoke
    assert "wav.setframerate(16_000)" in smoke
    assert 'wav.writeframes(b"\\0\\0" * 1_600)' in smoke
    assert 'assert 0 <= ready["rss_mb"] <= 4096' in smoke
    assert 'request("/livez", authenticated=False)' in smoke
    assert '"Bearer invalid"' in smoke
    assert 'assert transcription["result"] == {"kind": "no_speech", "segments": []}' in smoke
    assert "X-Mod-Engine-Pid" in smoke
    assert "X-Mod-Engine-Generation" in smoke
    assert "X-Mod-Engine-Load-Count" in smoke
    assert "assert resident_headers == first_headers" in smoke
    assert 'f"/v1/cancellations/{cancel_key}"' in smoke
    assert "threading.Thread" in smoke
    assert "assert cancellation.code == 204" in smoke
    assert '"engine did not recover after cancellation"' in smoke
    assert 'X-Mod-Engine-Generation"]) > int(' in smoke
    assert 'X-Mod-Engine-Load-Count"]) > int(' in smoke
    assert 'path.startswith("stt-whisper-")' in smoke


def test_whisper_cpu_release_inputs_are_immutable() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert (
        "FROM python:3.13.7-slim-bookworm@sha256:"
        "781449467ffb6f04218f09b1ecdcdc7d22b289ee5da9ec498b024e24ad7a6db7 "
        "AS runtime-base"
    ) in dockerfile
    assert (
        "FROM debian:bookworm-20250520-slim@sha256:"
        "364d3f277f79b11fafee2f44e8198054486583d3392e2472eb656d5c780156f5 "
        "AS whisper-builder"
    ) in dockerfile
    assert dockerfile.count("FROM python:3.13.7-slim-bookworm@sha256:") == 1
    assert "AS model-builder" in dockerfile
    assert "ARG WHISPER_CPP_COMMIT=306c88f4d1286aec1bf96e544632897886af5501" in dockerfile
    assert 'git -C /src/whisper.cpp checkout --detach "$WHISPER_CPP_COMMIT"' in dockerfile
    assert ":latest" not in dockerfile
    assert manifest["source"]["revision"] == "5359861c739e955e79d9a303bcbc70fb988958b1"


def test_whisper_cpu_build_disables_base_apt_sources_before_using_its_snapshot() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    setup, apt_update = dockerfile.split("apt-get -o Acquire::Check-Valid-Until=false update", 1)

    assert dockerfile.index("AS runtime-base") < dockerfile.index("AS whisper-builder")
    assert "RUN test -s /etc/ssl/certs/ca-certificates.crt" in setup
    assert (
        "COPY --from=runtime-base /etc/ssl/certs/ca-certificates.crt "
        "/etc/ssl/certs/ca-certificates.crt"
    ) in setup
    assert "https://snapshot.debian.org/" in setup
    assert "rm -f /etc/apt/sources.list" in setup
    assert "rm -rf /etc/apt/sources.list.d" in setup
    assert "snapshot.debian.org/archive/debian/20250601T000000Z" in setup
    assert "snapshot.debian.org" not in apt_update
    for package in (
        "ca-certificates=20230311",
        "cmake=3.25.1-1",
        "g++=4:12.2.0-3",
        "git=1:2.39.5-0+deb12u2",
        "make=4.3-4.1",
    ):
        assert package in apt_update
    for insecure_option in (
        "trusted=yes",
        "AllowInsecureRepositories",
        "AllowDowngradeToInsecureRepositories",
        "Verify-Peer=false",
        "Verify-Host=false",
        "--allow-insecure-repositories",
        "allow-unauthenticated",
        "--allow-unauthenticated",
        "APT::Get::AllowUnauthenticated",
    ):
        assert insecure_option not in dockerfile


def test_whisper_cpu_builder_is_static_internal_and_copied_into_the_python_runtime() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    for required in (
        "-DBUILD_SHARED_LIBS=OFF",
        "-DGGML_OPENMP=OFF",
        "-DWHISPER_CURL=OFF",
        "-static-libstdc++ -static-libgcc",
        "COPY --from=whisper-builder /src/whisper.cpp/build/bin/whisper-server "
        "/usr/local/bin/whisper-server",
        "COPY --from=model-builder --chown=whisper:whisper /models /models",
    ):
        assert required in dockerfile
    assert "FROM runtime-base AS model-builder" in dockerfile
    assert dockerfile.count("FROM runtime-base") == 2


def test_whisper_cpu_ci_collects_native_library_diagnostics_and_guards_failed_build_teardown() -> (
    None
):
    workflow = WORKFLOW.read_text(encoding="utf-8")
    smoke_job = workflow.split("  mod-whisper-cpu-smoke:", 1)[1].split(
        "  mod-whisper-cpu-release-evidence:", 1
    )[0]

    assert "Verify whisper-server runtime libraries" in smoke_job
    runtime_libraries = smoke_job.split("Verify whisper-server runtime libraries", 1)[1].split(
        "Verify selected Mod isolation and CPU configuration", 1
    )[0]
    assert "set -euo pipefail" in runtime_libraries
    assert (
        "exec -T mod-whisper-cpu test -x /usr/local/bin/whisper-server "
        ">> mod-whisper-cpu-ldd.txt 2>&1" in runtime_libraries
    )
    assert (
        "exec -T mod-whisper-cpu ldd /usr/local/bin/whisper-server "
        ">> mod-whisper-cpu-ldd.txt 2>&1" in runtime_libraries
    )
    assert "mod-whisper-cpu-ldd.txt" in smoke_job
    assert "! grep -Eiq 'libgomp|not found' mod-whisper-cpu-ldd.txt" in runtime_libraries
    assert "| tee" not in runtime_libraries
    assert "|| true" not in runtime_libraries
    assert "test -s mod-whisper-cpu-ldd.txt" in runtime_libraries
    artifact_collection = smoke_job.split("Collect Compose smoke diagnostics", 1)[1].split(
        "Upload Mod smoke artifacts", 1
    )[0]
    assert "- name: Collect Compose smoke diagnostics\n        if: always()" in smoke_job
    assert "test -f mod-whisper-cpu-ldd.txt && mv mod-whisper-cpu-ldd.txt" in artifact_collection
    teardown = smoke_job.split("Tear down selected Compose smoke", 1)[1]
    assert 'digest="${STT_MOD_WHISPER_CPU_DIGEST:-}"' in teardown
    assert (
        "STT_MOD_WHISPER_CPU_DIGEST is absent or malformed; skipping Compose teardown" in teardown
    )
    assert "sha256:????????????????????????????????????????????????????????????????)" in teardown
    assert (
        'test "$digest" != "sha256:'
        '0000000000000000000000000000000000000000000000000000000000000000"' in teardown
    )
    assert "docker compose" in teardown


def test_whisper_cpu_contract_artifact_is_generated_from_the_core_contract() -> None:
    wrapper = ast.parse(MOD_CONTRACT_WRAPPER.read_text(encoding="utf-8"))

    assert not [node for node in wrapper.body if isinstance(node, ast.ClassDef)]
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "contract_v1_artifact"
        for node in wrapper.body
    )
    assert MOD_CONTRACT_ARTIFACT.read_text(encoding="utf-8") == CORE_CONTRACT.read_text(
        encoding="utf-8"
    )

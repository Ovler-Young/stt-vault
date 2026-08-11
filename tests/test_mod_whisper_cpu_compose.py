import hashlib
import json
import re
from pathlib import Path

import yaml

from stt_vault.core.models.mod_contracts import TranscriptionRequestV1

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "mods/mod-whisper-cpu/Dockerfile"
COMPOSE = ROOT / "docker-compose.mod-whisper-cpu.yml"
WORKFLOW = ROOT / ".github/workflows/docker.yml"
MANIFEST = ROOT / "mods/mod-whisper-cpu/model-manifest.json"
GO_MOD = ROOT / "mods/mod-whisper-cpu/gateway/go.mod"
FIXTURE = ROOT / "mods/mod-whisper-cpu/fixtures/gateway-v1.json"
APK_LOCK = ROOT / "mods/mod-whisper-cpu/alpine-apk-lock.v1.json"


def test_scratch_image_has_pinned_static_builds_and_one_selected_model() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM --platform=linux/amd64 golang:1.24.0-alpine3.21@sha256:" in dockerfile
    assert (
        "alpine:3.22.1@sha256:eafc1edb577d2e9b458664a15f23ea1c370214193226069eb22921169fc7e43f"
        in dockerfile
    )
    assert "apk add --no-network /var/cache/apk/*.apk" in dockerfile
    assert dockerfile.count("apk add --no-network /var/cache/apk/*.apk") == 2
    assert "apk update" not in dockerfile
    assert "--allow-untrusted" not in dockerfile
    assert "trusted" not in dockerfile
    assert "CGO_ENABLED=0 GOOS=linux GOARCH=amd64" in dockerfile
    assert "-trimpath" in dockerfile
    assert "-ldflags='-s -w -buildid='" in dockerfile
    assert "-static -s" in dockerfile
    assert "GGML_OPENMP=OFF" in dockerfile
    assert "WHISPER_CURL=OFF" in dockerfile
    assert "readelf -l" in dockerfile
    assert "readelf -d" in dockerfile
    assert "libgomp" in dockerfile
    assert "FROM scratch" in dockerfile
    final = dockerfile.split("FROM scratch", 1)[1]
    assert "COPY --from=gateway-builder /out/gateway /gateway" in final
    assert "COPY --from=whisper-builder /src/build/bin/whisper-server /whisper-server" in final
    assert (
        "COPY --from=model-builder /models/${WHISPER_MODEL_ID} /models/${WHISPER_MODEL_ID}" in final
    )
    assert "COPY model-manifest.json /model-manifest.json" in final
    for forbidden in ("python", "pip", "apk", "curl", "shell", "ca-certificates", "tzdata"):
        assert forbidden not in final.lower()
    assert "USER 10001:10001" in final
    healthcheck = "HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=30s"
    assert f'{healthcheck} CMD ["/gateway", "healthcheck"]' in final


def test_go_gateway_is_stdlib_only() -> None:
    go_mod = GO_MOD.read_text(encoding="utf-8")
    assert "go 1.24.0" in go_mod
    assert "require" not in go_mod
    assert not (GO_MOD.parent / "go.sum").exists()


def test_gateway_hash_fixtures_match_the_pydantic_request_oracle() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in fixture["hash_cases"]:
        request = TranscriptionRequestV1.model_validate(case["request"])
        digest = hashlib.sha256()
        digest.update(request.model_dump_json().encode("utf-8"))
        digest.update(bytes.fromhex(case["audio_hex"]))
        assert digest.hexdigest() == case["sha256"]


def test_compose_preserves_the_selected_two_service_isolation_without_model_volume() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"stt-vault", "mod-whisper-cpu"}
    mod = compose["services"]["mod-whisper-cpu"]
    assert mod["networks"] == ["stt-mods"]
    assert mod["cpus"] == "2.0"
    assert mod["mem_limit"] == "4g"
    assert mod["pids_limit"] == 256
    assert mod["user"] == "10001:10001"
    assert mod["read_only"] is True
    assert mod["tmpfs"] == ["/tmp:rw,noexec,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=1777"]
    assert "volumes" not in mod
    assert "ports" not in mod
    assert "gpus" not in mod
    assert mod["secrets"] == [
        {"source": "stt_mod_token", "uid": "10001", "gid": "10001", "mode": 0o400}
    ]


def test_alpine_apk_lock_is_complete_and_docker_uses_only_locked_artifacts() -> None:
    lock = json.loads(APK_LOCK.read_text(encoding="utf-8"))
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert lock["schema"] == 1
    assert lock["platform"] == "linux/amd64"
    assert lock["alpine"]["version"] == "3.22"
    assert set(lock["alpine"]["repositories"]) == {"main", "community"}
    assert len(lock["packages"]) >= 40
    names = [package["name"] for package in lock["packages"]]
    assert len(names) == len(set(names))
    for repository in lock["alpine"]["repositories"].values():
        assert repository["url"].startswith("https://dl-cdn.alpinelinux.org/alpine/v3.22/")
        assert re.fullmatch(r"[a-f0-9]{64}", repository["sha256"])
    for package in lock["packages"]:
        assert package["repository"] in {"main", "community"}
        assert package["filename"] == f"{package['name']}-{package['version']}.apk"
        assert package["url"].startswith(
            f"https://dl-cdn.alpinelinux.org/alpine/v3.22/{package['repository']}/x86_64/"
        )
        assert package["url"].endswith(f"/{package['name']}-{package['version']}.apk")
        assert re.fullmatch(r"[a-f0-9]{64}", package["sha256"])
        assert package["url"] in dockerfile
        assert f"--checksum=sha256:{package['sha256']}" in dockerfile

    locked_capabilities = {
        capability.split("=", 1)[0]
        for package in lock["packages"]
        for capability in [package["name"], *package["provides"]]
    }
    dependency_names = {
        dependency.lstrip("!").split("=", 1)[0].split("<", 1)[0].split(">", 1)[0]
        for package in lock["packages"]
        for dependency in package["dependencies"]
        if not dependency.startswith("!")
    }
    assert dependency_names <= locked_capabilities


def test_model_manifest_is_selected_at_build_time() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert (
        manifest["models"]["ggml-base.en.bin"]["sha256"]
        == "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"
    )
    assert re.fullmatch(r"[a-f0-9]{40}", manifest["source"]["revision"])


def test_ci_runs_go_gates_and_outside_image_smoke_with_static_inspection() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/setup-go@d35c59abb061a4a6fb18e82ac0862c26744d6ab5 # v5.5.0" in workflow
    for command in (
        "gofmt -d",
        "go test ./...",
        "go test -race ./...",
        "go vet ./...",
        "go test -run=^$ -fuzz=Fuzz -fuzztime=5s ./internal/contract",
    ):
        assert command in workflow
    assert "docker run --rm --network" in workflow
    assert "mod_whisper_cpu_smoke.py" in workflow
    assert "python /work/tests/e2e/fixtures/mod_whisper_cpu_smoke.py" in workflow
    assert "readelf -l image-export/gateway image-export/whisper-server" in workflow
    assert "readelf -d image-export/gateway image-export/whisper-server" in workflow
    assert "find image-export" in workflow
    assert "ReadonlyRootfs == true" in workflow
    assert (
        'HostConfig.Tmpfs["/tmp"] == '
        '"rw,noexec,nosuid,nodev,size=512m,uid=10001,gid=10001,mode=1777"' in workflow
    )
    assert "test -s jfk.wav" in workflow
    assert '"$PWD/jfk.wav:/tmp/jfk.wav:ro"' in workflow
    app_smoke = ROOT / "tests/e2e/fixtures/app_mod_whisper_cpu_smoke.py"
    assert 'Path("/tmp/jfk.wav")' in app_smoke.read_text(encoding="utf-8")
    assert "syft" in workflow.lower()
    assert "trivy" in workflow.lower()

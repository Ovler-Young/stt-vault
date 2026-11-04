import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/docker.yml"
PINS = ROOT / ".github/trust/workflow-action-pins.v1.json"
USES = re.compile(
    r"^\s*uses:\s*(?P<repository>[^@\s]+)@(?P<commit>[0-9A-Fa-f]{40})\s+#\s+(?P<tag>\S+)\s*$"
)


def test_docker_workflow_uses_only_verified_immutable_action_commits() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pins = json.loads(PINS.read_text(encoding="utf-8"))

    assert yaml.safe_load(workflow)
    assert pins["schema"] == "stt-vault.workflow-action-pins"
    assert pins["schema_version"] == 1
    assert pins["workflow"] == ".github/workflows/docker.yml"

    expected = {(pin["repository"], pin["tag"]): pin["commit"] for pin in pins["actions"]}
    assert len(expected) == len(pins["actions"])
    assert all(re.fullmatch(r"[0-9a-f]{40}", commit) for commit in expected.values())
    assert all(
        re.fullmatch(r"[0-9a-f]{40}", pin["tag_object"])
        for pin in pins["actions"]
        if "tag_object" in pin
    )

    actual = {}
    for line in workflow.splitlines():
        if not line.lstrip().startswith("uses:"):
            continue
        value = line.split("uses:", 1)[1].strip()
        if value.startswith("./"):
            continue
        match = USES.fullmatch(line)
        assert match, f"external action is not a pinned commit with a tag comment: {line}"
        actual[(match["repository"], match["tag"])] = match["commit"].lower()

    assert actual == expected

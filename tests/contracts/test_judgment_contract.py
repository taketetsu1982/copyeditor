import hashlib
import re
from collections.abc import Mapping
from pathlib import Path

import pytest
from fastmcp import Client
from copyeditor.config import load_config, strict_yaml

from .harness import load_cases
from tests.integration.test_transport import setup

CONTRACT = Path(__file__).resolve().parents[2] / "contracts/tools.md"


def test_legacy_case_bytes_and_inventory_are_preserved():
    raw = re.findall(r"```json contract-case\n(.*?)\n```", CONTRACT.read_text(), re.S)
    assert len(load_cases(CONTRACT, "contract-case")) == len(raw) == 31
    assert hashlib.sha256("\n".join(raw).encode()).hexdigest() == (
        "7510b95614388033583dc6e805f3622a8ff9bbe7b655baea2ebeb41f22edb4ae")


def test_action_shape_and_public_classification_agree():
    contract = CONTRACT.read_text()
    metadata = contract.split("---", 2)[1]
    assert "revision: 9" in metadata and "base-revision" not in metadata
    assert "AC-08-18" in metadata
    registry = contract.split("### Registered threshold classification\n")[1].split("### Detection")[0]
    actions = re.findall(r'"([a-z_]+)"', re.search(r"^Action = (.+)$", contract, re.M)[1])
    table = registry.split("| Action | Bounded editing intent |\n")[1].split("\n\n")[0]
    rows = re.findall(r"^\| ([a-z_]+) \|", table, re.M)
    assert len(actions) == len(rows) == 7 and set(actions) == set(rows)
    assert "p >= 0.53: present; p < 0.53: absent" in registry
    assert "Confidence, including zero, cannot change eligibility" in registry
    assert "stiff→simplify_vocabulary" in registry and "repetitive→simplify_structure" in registry


def test_public_model_called_table_counts_generations_not_estimates():
    section = CONTRACT.read_text().split("### V3 model_called invariant\n")[1].split("### Judgment accounting")[0]
    assert "model_called == any(row.model_calls > 0 for row in providers)" in section
    rows = re.findall(r"^\| [^|]+ \| (\d+) \| (\d+) \| (\d+) \| (true|false) \|$", section, re.M)
    assert len(rows) == 5 and ("0", "0", "1", "false") in rows
    for editing, judgment, estimation, called in rows:
        assert (called == "true") == (int(editing) + int(judgment) > 0)


def schema_versions(value):
    if isinstance(value, dict):
        if "schema_version" in value.get("properties", {}):
            yield value["properties"]["schema_version"]["const"]
        for child in value.values():
            yield from schema_versions(child)
    elif isinstance(value, list):
        for child in value:
            yield from schema_versions(child)


@pytest.mark.asyncio
async def test_current_discovery_does_not_advertise_judgment(setup):
    make, created, records = setup
    server, _, _ = make()
    async with Client(server) as client:
        tools = await client.list_tools()
    assert {tool.name: set(schema_versions(tool.output_schema)) for tool in tools} == {
        "polish_text": {1, 2}, "lint_text": {1}}
    assert all("copyeditor.judgment=on" not in (tool.description or "") for tool in tools)
    assert created == records == []


CONFIG_CONTRACT = CONTRACT.with_name("config.md")


def test_legacy_config_contract_is_preserved():
    contract = CONFIG_CONTRACT.read_text()
    legacy = re.sub(r"\n### [^\n]+\n.*?(?=\n## |\Z)", "", contract, flags=re.S)
    legacy = legacy.replace("revision: 7", "revision: 2")
    legacy = re.sub(r", AC-08-\d+", "", legacy)
    legacy = legacy.replace("This table and the judgment fields table define", "This table is")
    legacy = legacy.replace("`credentials`, `judgment.credentials`;", "`credentials`;")
    assert hashlib.sha256(legacy.encode()).hexdigest() == (
        "0ada1e2a0daadfc9ca5e5fabd669d69e0e48116aa9646e7abf0ad7852bda1a9c")


def test_judgment_config_example_and_leaf_contract_agree():
    contract = CONFIG_CONTRACT.read_text()
    metadata = contract.split("---", 2)[1]
    assert "revision: 7" in metadata and "base-revision" not in metadata
    example = strict_yaml(re.search(r"```yaml\n(.*?)\n```", contract, re.S)[1])["judgment"]
    rows = re.findall(r"^\| judgment\.([a-z_]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$", contract, re.M)
    assert len(rows) == len(example) == 10
    assert {key for key, *_ in rows} == set(example)
    assert not {"provider", "key", "token", "secret", "endpoint"} & set(example)
    for key, accepted, variable, default in rows:
        assert variable.strip() == "COPYEDITOR_JUDGMENT_" + key.upper()
        assert strict_yaml("value: " + default.strip())["value"] == example[key]
    assert example["enabled"] is False and example["model"] == "jev-1.13.0"
    assert (example["policy_version"], example["thresholds_version"]) == (
        "reference-gate-action-v1", "gate-verify-v1")
    assert all(example[key] in CONTRACT.read_text() for key in ("policy_version", "thresholds_version"))
    assert "tools.md#registered-threshold-classification" in contract
    assert "ctr01-us08-draft" not in contract
    assert "reject that reference before looking up its value" in contract
    assert "A disabled process never looks up, validates, stores or requires TYPESAFE_API_KEY" in contract


def test_judgment_credential_errors_use_declared_fixed_label():
    contract = CONFIG_CONTRACT.read_text()
    labels = set(re.findall(r"`([^`]+)`", re.search(r"fixed labels ([^;]+);", contract)[1]))
    assert labels == {"config", "rules", "credentials", "judgment.credentials"}
    amendment = contract.split("### Secrets and startup errors amendment\n")[1].split("## Image layout")[0]
    errors = re.findall(r"(missing_required|invalid_config|credentials_unavailable) at ([a-z_]+(?:\.[a-z_]+)+)", amendment)
    assert set(errors) == {(code, "judgment.credentials") for code in (
        "missing_required", "invalid_config", "credentials_unavailable")}
    assert all(label in labels for _, label in errors)


class NoJudgmentSecret(Mapping):
    def __getitem__(self, key):
        assert key != "TYPESAFE_API_KEY", "Disabled configuration must not access judgment credentials"
        if key == "GOOGLE_CLOUD_PROJECT":
            return "project"
        raise KeyError(key)

    def __iter__(self):
        raise AssertionError("Configuration must not enumerate the secret environment")

    def __len__(self):
        return 1


def test_current_config_does_not_read_judgment_secret(tmp_path):
    config = load_config(tmp_path / "absent", NoJudgmentSecret())
    assert config["provider"] == "vertex"
    assert config["judgment.enabled"] is False
    for value in ("", "invalid key\n"):
        env = {"GOOGLE_CLOUD_PROJECT": "project", "TYPESAFE_API_KEY": value}
        assert load_config(tmp_path / "absent", env).values == config.values

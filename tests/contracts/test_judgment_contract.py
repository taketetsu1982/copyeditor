import hashlib
import re
from pathlib import Path

import pytest
from fastmcp import Client

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
    assert "revision: 8" in metadata and "base-revision" not in metadata
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

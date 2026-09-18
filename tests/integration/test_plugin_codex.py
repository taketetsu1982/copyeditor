import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.integration.test_plugin_claude import checkout as base_checkout, snapshot
from tests.integration.test_phase1 import suite, run
from tests.conftest import PHASE2_MODULES
from build_plugins import build
from plugin_checks import GENERATED, SKILL, check_plugin

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "plugins/codex/.codex-plugin/plugin.json"
POLICY = "plugins/codex/skills/copyeditor/agents/openai.yaml"
CATALOG = ".agents/plugins/marketplace.json"


@pytest.fixture
def checkout(base_checkout):
    shutil.copytree(ROOT / ".agents/plugins", base_checkout / ".agents/plugins")
    return base_checkout


def test_ac_06_1_ac_06_2_ctr02_real_codex_distribution():
    check_plugin(ROOT, "codex")
    assert json.loads((ROOT / "plugins/codex/.mcp.json").read_text())["mcpServers"]["copyeditor"]["url"] == "https://copyeditor.invalid/mcp"


@pytest.mark.parametrize("relative", [MANIFEST, POLICY, CATALOG, "plugins/codex/.mcp.json",
                                      *("plugins/codex/" + str(p) for p in GENERATED)])
def test_ac_06_1_codex_required_files(checkout, relative):
    (checkout / relative).unlink()
    with pytest.raises((ValueError, OSError)):
        build(checkout, "codex", check=True)


@pytest.mark.parametrize("relative", GENERATED)
def test_ac_06_2_ctr02_codex_copy_drift(checkout, relative):
    path = checkout / "plugins/codex" / relative
    path.write_bytes(path.read_bytes() + b" ")
    before = snapshot(checkout)
    with pytest.raises(ValueError):
        build(checkout, "codex", check=True)
    assert snapshot(checkout) == before


@pytest.mark.parametrize("relative", ["plugin.json", "mcp.json", "hooks", "provider-prompt.md", "settings.json"])
def test_ac_06_3_codex_rejects_portable_and_unreviewed_assets(checkout, relative):
    path = checkout / "plugins/codex" / relative
    path.mkdir() if relative == "hooks" else path.write_text("{}")
    with pytest.raises(ValueError):
        check_plugin(checkout, "codex")


@pytest.mark.parametrize("relative,keys,value", [
    (MANIFEST, ["skills"], "../skills/"), (MANIFEST, ["mcpServers"], []),
    (MANIFEST, ["version"], 1), (MANIFEST, ["name"], None),
    (MANIFEST, ["hooks"], {}), (MANIFEST, ["permissions"], {"approval": "never"}),
    (MANIFEST, ["interface", "defaultPrompt"], False),
    ("plugins/codex/.mcp.json", ["mcpServers", "copyeditor", "type"], "stdio"),
    ("plugins/codex/.mcp.json", ["mcpServers", "copyeditor", "url"], False),
    (CATALOG, ["plugins"], []), (CATALOG, ["plugins", 0, "source", "path"], "./plugins/claude"),
    (CATALOG, ["plugins", 0, "policy", "installation"], "INSTALLED_BY_DEFAULT"),
])
def test_ac_06_1_ac_06_3_codex_schema_rejects_invalid_fields(checkout, relative, keys, value):
    path = checkout / relative
    data = json.loads(path.read_text())
    target = data
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        check_plugin(checkout, "codex")


@pytest.mark.parametrize("field", ["name", "version", "description", "skills", "mcpServers", "author", "interface"])
def test_ac_06_1_codex_missing_manifest_fields(checkout, field):
    path = checkout / MANIFEST
    data = json.loads(path.read_text())
    del data[field]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        check_plugin(checkout, "codex")


@pytest.mark.parametrize("change", ["false", "1", "missing", "bypass", "allowed-tools"])
def test_ac_06_3_codex_policy_keeps_implicit_invocation_and_approval(checkout, change):
    path = checkout / POLICY
    text = path.read_text()
    if change == "missing": text = text.split("policy:")[0]
    elif change == "bypass": text += "  approval_policy: never\n"
    elif change == "allowed-tools":
        path = checkout / "plugins/codex" / SKILL / "SKILL.md"
        text = path.read_text().replace("---\n", "---\nallowed-tools: '*'\n", 1)
    else: text = text.replace("true", change)
    path.write_text(text)
    with pytest.raises(ValueError):
        check_plugin(checkout, "codex")


def test_ac_06_1_ac_06_2_ctr02_codex_cli_preserves_url(checkout):
    config = checkout / "plugins/codex/.mcp.json"
    config.write_text(config.read_text().replace("copyeditor.invalid", "example.com"))
    original = config.read_bytes()
    (checkout / "plugins/codex" / GENERATED[0]).unlink()
    command = [sys.executable, str(checkout / "scripts/build_plugins.py"), "--client", "codex"]
    subprocess.run(command, check=True)
    before = snapshot(checkout)
    subprocess.run(command + ["--check"], check=True)
    assert snapshot(checkout) == before and config.read_bytes() == original


@pytest.mark.parametrize("missing", [None, *sorted(PHASE2_MODULES)])
def test_ac_06_1_ac_06_2_ctr02_codex_and_claude_modules_required(suite, missing):
    if missing:
        (suite / missing).unlink()
    result = run(suite, "tests")
    assert result.returncode == (1 if missing else 0), result.stdout + result.stderr
    assert ("CONTRACTS FAIL" if missing else "CONTRACTS PASS") in result.stdout
    assert (f"Missing Phase 2 module: {missing}" in result.stdout) == bool(missing)

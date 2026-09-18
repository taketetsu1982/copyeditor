import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from build_plugins import build
from plugin_checks import GENERATED, SKILL, check_plugin


@pytest.fixture
def checkout(tmp_path):
    for directory in ("rules", "skills", "plugins", ".claude-plugin", "scripts", "src"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    return tmp_path


def snapshot(root):
    return {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def test_ac_06_1_ac_06_2_ctr02_real_claude_distribution():
    check_plugin(ROOT, "claude")
    assert json.loads((ROOT / "plugins/claude/.mcp.json").read_text())["mcpServers"]["copyeditor"]["url"] == "https://copyeditor.invalid/mcp"


@pytest.mark.parametrize("relative", [".claude-plugin/plugin.json", ".mcp.json", *map(str, GENERATED)])
def test_ac_06_1_missing_package_file_fails(checkout, relative):
    (checkout / "plugins/claude" / relative).unlink()
    with pytest.raises((ValueError, OSError)):
        build(checkout, "claude", check=True)


@pytest.mark.parametrize("relative", GENERATED)
def test_ac_06_2_ctr02_copy_drift_is_detected_without_writes(checkout, relative):
    path = checkout / "plugins/claude" / relative
    path.write_bytes(path.read_bytes() + b" ")
    before = snapshot(checkout)
    with pytest.raises(ValueError):
        build(checkout, "claude", check=True)
    assert snapshot(checkout) == before


@pytest.mark.parametrize("relative,is_directory", [("hooks", True), ("hooks/hooks.json", False),
    ("settings.json", False), ("provider-prompt.md", False), ("agents/agent.md", False)])
def test_ac_06_3_unknown_assets_and_empty_hooks_fail(checkout, relative, is_directory):
    path = checkout / "plugins/claude" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir() if is_directory else path.write_text("{}")
    with pytest.raises(ValueError):
        check_plugin(checkout, "claude")


@pytest.mark.parametrize("field,value", [("hooks", {}), ("allowed-tools", ["*"]),
    ("permissions", {"defaultMode": "bypassPermissions"}), ("version", 1),
    ("skills", "../skills/"), ("mcpServers", []), ("name", None)])
def test_ac_06_1_ac_06_3_manifest_schema_rejects_unsafe_settings(checkout, field, value):
    path = checkout / "plugins/claude/.claude-plugin/plugin.json"
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        check_plugin(checkout, "claude")


@pytest.mark.parametrize("extra", ["allowed-tools: '*'", "disable-model-invocation: true", "permissionMode: bypassPermissions"])
def test_ac_06_3_skill_cannot_bypass_approval_or_disable_invocation(checkout, extra):
    path = checkout / "plugins/claude" / SKILL / "SKILL.md"
    path.write_text(path.read_text().replace("---\n", "---\n" + extra + "\n", 1))
    with pytest.raises(ValueError):
        check_plugin(checkout, "claude")


def test_ac_06_1_ac_06_2_ctr02_cli_rebuild_and_check_preserve_operator_url(checkout):
    config = checkout / "plugins/claude/.mcp.json"
    config.write_text(config.read_text().replace("copyeditor.invalid", "example.com"))
    original = config.read_bytes()
    (checkout / "plugins/claude" / GENERATED[1]).unlink()
    command = [sys.executable, str(checkout / "scripts/build_plugins.py"), "--client", "claude"]
    assert subprocess.run(command + ["--check"], capture_output=True).returncode == 1
    subprocess.run(command, check=True)
    before = snapshot(checkout)
    subprocess.run(command + ["--check"], check=True)
    assert snapshot(checkout) == before and config.read_bytes() == original


@pytest.mark.parametrize("relative,content", [
    ("plugins/claude/.mcp.json", '{"mcpServers":{"copyeditor":{"type":"stdio","url":"https://example.com/mcp"}}}'),
    ("plugins/claude/.mcp.json", '{"mcpServers":{"copyeditor":{"type":"http","url":false}}}'),
    ("plugins/claude/.claude-plugin/plugin.json", '{}'),
    (".claude-plugin/marketplace.json", '{"name":"copyeditor-local","plugins":[]}'),
    ("plugins/claude/.claude-plugin/plugin.json", '{"name":"copyeditor","name":"other"}'),
])
def test_ac_06_1_schema_and_marketplace_fail_closed(checkout, relative, content):
    (checkout / relative).write_text(content)
    with pytest.raises(ValueError):
        check_plugin(checkout, "claude")


def test_ac_06_2_ctr02_source_change_and_symlink_cannot_hide_drift(checkout):
    source = checkout / SKILL / "SKILL.md"
    source.write_bytes(source.read_bytes() + b"Changed source.\n")
    with pytest.raises(ValueError):
        check_plugin(checkout, "claude")
    target = checkout / "plugins/claude" / GENERATED[0]
    target.unlink()
    target.symlink_to(source)
    with pytest.raises(ValueError):
        build(checkout, "claude")

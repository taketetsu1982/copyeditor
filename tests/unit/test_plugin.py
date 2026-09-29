import json
import re
import tomllib
from pathlib import Path

from copyeditor.providers.vertex import MODEL_ERROR
from copyeditor.server import INPUT_ERROR, READER_LIMIT, TEXT_LIMIT

ROOT = Path(__file__).parents[2]
PLUGIN = ROOT / "plugin"


def manifest():
    return json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))


def skill():
    text = (PLUGIN / "skills/copyeditor/SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", text)
    assert match, "SKILL.md must start with name and description front matter"
    return match.group(1), match.group(2), text


def test_manifest_matches_the_upload_layout_and_package_version():
    data = manifest()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert data["name"] == "copyeditor" and data["version"] == version
    assert (PLUGIN / data["skills"]).is_dir()
    # The claude.ai connector already provides the server; bundling it would list polish_text twice.
    assert "mcpServers" not in data


def test_skill_front_matter_fits_the_skill_limits():
    name, description, _ = skill()
    assert re.fullmatch(r"[a-z0-9-]{1,64}", name) and name == "copyeditor"
    assert 1 <= len(description) <= 1024


def test_skill_repeats_the_server_limits_and_messages_verbatim():
    _, _, text = skill()
    assert f"{TEXT_LIMIT:,}" in text and f"up to {READER_LIMIT}" in text
    for message in (INPUT_ERROR, MODEL_ERROR, "Authentication failed."):
        assert f"`{message}`" in text

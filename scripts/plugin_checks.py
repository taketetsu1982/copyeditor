"""Validate the supported distribution subset; reject unreviewed package settings."""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from copyeditor.config import strict_yaml
from copyeditor.rules import load_rules

SKILL = Path("skills/copyeditor")
GENERATED = (SKILL / "SKILL.md", SKILL / "rules/common.md", SKILL / "rules-version.json")


def require(condition):
    if not condition:
        raise ValueError("invalid plugin distribution")


def object_pairs(pairs):
    require(len(dict(pairs)) == len(pairs))
    return dict(pairs)


def read_json(path):
    return json.loads(path.read_bytes(), object_pairs_hook=object_pairs)


def skill_body(raw):
    require(raw.startswith(b"---\n"))
    front, body = raw[4:].split(b"\n---\n", 1)
    data = strict_yaml(front.decode())
    require(isinstance(data, dict) and {"name", "description"} <= data.keys())
    require(data.keys() <= {"name", "description", "disable-model-invocation"})
    require(data["name"] == "copyeditor" and isinstance(data["description"], str) and data["description"].strip())
    require(data.get("disable-model-invocation", False) is False)
    return body


def generated_content(root):
    raw = (root / SKILL / "SKILL.md").read_bytes()
    skill_body(raw)
    snapshot = load_rules(root / "rules", overlay=None)
    versions = {"rules_version": snapshot.rules_version, "common_version": snapshot.common_version}
    return dict(zip(GENERATED, (raw, snapshot.common_bytes, (json.dumps(versions, indent=2) + "\n").encode())))


def check_layout(root, client, generated_required=True):
    require(client == "claude")
    package = root / "plugins" / client
    fixed = {Path(".claude-plugin/plugin.json"), Path(".mcp.json")}
    allowed = fixed | set(GENERATED)
    directories = {p for f in allowed for p in f.parents if p != Path(".")}
    require(not (root / "plugins").is_symlink() and not package.is_symlink() and package.is_dir())
    for entry in package.rglob("*"):
        require(not entry.is_symlink())
        require(entry.relative_to(package) in (directories if entry.is_dir() else allowed))
    require(all((package / f).is_file() for f in (allowed if generated_required else fixed)))
    manifest = read_json(package / ".claude-plugin/plugin.json")
    require(set(manifest) == {"name", "version", "description", "skills", "mcpServers"})
    require(manifest["name"] == "copyeditor" and manifest["skills"] == "./skills/" and manifest["mcpServers"] == "./.mcp.json")
    require(isinstance(manifest["version"], str) and re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]))
    require(isinstance(manifest["description"], str) and manifest["description"].strip())
    connection = read_json(package / ".mcp.json")
    require(set(connection) == {"mcpServers"} and set(connection["mcpServers"]) == {"copyeditor"})
    server = connection["mcpServers"]["copyeditor"]
    require(set(server) == {"type", "url"} and server["type"] == "http" and isinstance(server["url"], str))
    url = urlsplit(server["url"])
    require(url.scheme == "https" and url.hostname and not url.username and not url.password and not url.query and not url.fragment)
    catalog = root / ".claude-plugin/marketplace.json"
    require(not catalog.parent.is_symlink() and not catalog.is_symlink())
    require(read_json(catalog) == {"name": "copyeditor-local", "owner": {"name": "copyeditor maintainers"},
                                  "plugins": [{"name": "copyeditor", "source": "./plugins/claude"}]})
    return package


def check_plugin(root, client):
    package = check_layout(root, client)
    for relative, expected in generated_content(root).items():
        actual = (package / relative).read_bytes()
        if relative == SKILL / "SKILL.md":
            require(skill_body(actual) == skill_body(expected))
        else:
            require(actual == expected)

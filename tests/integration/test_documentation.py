import re
from pathlib import Path

from copyeditor.config import SCHEMA, load_config

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
EN, JA = README.split("## \u65e5\u672c\u8a9e\n")


def test_ac_05_7_ctr04_bilingual_structure_and_commands():
    english = re.findall(r"^## (.+)$", EN, re.M)
    japanese = re.findall(r"^### (.+)$", JA, re.M)
    assert len(english) == len(japanese) == 8
    assert japanese == ["\u6e96\u5099\u3068Vertex AI","\u8a2d\u5b9a","\u30ed\u30fc\u30ab\u30ebDocker: none","Google\u8a8d\u8a3c","\u30af\u30e9\u30a4\u30a2\u30f3\u30c8\u63a5\u7d9a","\u6d3e\u751fimage\u3068\u8a00\u8a9e\u30eb\u30fc\u30eb","\u4fdd\u6301\u7bc4\u56f2\u3068\u904b\u7528\u30ed\u30b0","\u30e9\u30a4\u30bb\u30f3\u30b9\u3068\u8b1d\u8f9e"]
    assert english == ["Setup and Vertex AI", "Configuration", "Local Docker: none",
                       "Google authentication", "Client connection", "Derived images and language rules",
                       "Retention and operational logs", "License and acknowledgements"]
    assert re.findall(r"```.*?```", EN, re.S) == re.findall(r"```.*?```", JA, re.S)
    assert re.findall(r"^\|.*$", EN, re.M) == re.findall(r"^\|.*$", JA, re.M)


def test_ac_05_7_ctr04_public_paths_and_real_config():
    for section in (EN, JA):
        links = re.findall(r"\]\(([^)]+)\)", section)
        assert {"contracts/config.md#fields", "contracts/tools.md", "rules/README.md#overlays", "LICENSE"} <= set(links)
        for link in links:
            if "://" not in link:
                path, _, anchor = link.partition("#")
                assert "docs" not in Path(path).parts and (ROOT / path).exists()
                if anchor:
                    headings = re.findall(r"^#+ (.+)$", (ROOT / path).read_text(), re.M)
                    assert anchor in [heading.lower().replace(" ", "-") for heading in headings]
        for field, definition in SCHEMA.items():
            assert f"`{field}`" in section and f"`{definition[0]}`" in section
        for term in ("/etc/copyeditor/config.yaml", "/etc/copyeditor/rules.d/ja.md", "65532", "MIT"):
            assert term in section
    assert load_config(ROOT / "config.example.yaml", {"GOOGLE_CLOUD_PROJECT": "fixture"})["auth.mode"] == "none"


def test_ac_05_7_ctr04_authentication_and_retention_in_both_languages():
    for section in (EN, JA):
        for term in ("GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_SIGNING_KEY", "32 UTF-8 bytes", "HTTP 401",
                     "authorized redirect URI", "/auth/callback", "MemoryStore", "scale-to-zero",
                     "stdout", "stderr", "provider", "Vertex AI", "httpRequest.requestUrl", "_Default",
                     "polish_text", "lint_text", "natural-japanese", "copyeditor-provenance-v1"):
            assert term in section
        assert 'httpRequest.requestUrl=~"/auth/callback([?]|$)"' in section


def test_ac_06_1_ac_06_6_ctr02_plugin_instructions_in_both_languages():
    assert re.findall(r"^### (.+)$", EN, re.M) == ["Claude Code plugin", "Codex plugin", "Submission permission and results"]
    assert re.findall(r"^#### (.+)$", JA, re.M) == ["Claude Code plugin", "Codex plugin", "送信許可と結果の扱い"]
    for section in (EN, JA):
        for client, install in (("claude", "install"), ("codex", "add")):
            assert f"plugins/{client}/.mcp.json" in section
            assert section.count(f"{client} plugin marketplace add .") == 1
            assert section.count(f"{client} plugin {install} copyeditor@copyeditor-local") == 1
        for term in ("CLAUDE.md", "AGENTS.md", "Vertex AI", "OAuth", "flag", "rules/common.md#meaning-and-adoption"):
            assert term in section
        assert "deployment/" not in section

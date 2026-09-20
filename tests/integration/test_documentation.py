"""Static bilingual guidance observations, not live/client acceptance.
AC-08-1: disabled-mode requirements; AC-08-6: no adoption or meaning guarantee.
AC-08-11, AC-08-18: additional destination and direct-MCP consent limits.
AC-08-17: documented discovery, permission scope and reporting."""
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
            if field.startswith("judgment."):
                contract = (ROOT / "contracts/config.md").read_text()
                assert re.search(r"^\| " + re.escape(field) + r" \|[^\n]*\| " + definition[0] + r" \|", contract, re.M)
                assert "contracts/config.md#judgment-fields" in section
            else:
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


def test_ac_07_1_ac_07_2_ac_07_7_ac_07_10_ac_07_12_rewrite_guidance_matches_both_languages():
    import json
    for section in (EN, JA):
        for term in ("degree=polish", "degree=rewrite", "schema_version=2", "status=issue", "status=no_issue",
                     "12,000", "1,000", "4,000", "16,000", "262,144", "6,000", "240",
                     "input_limit", "generation_truncated", "output_limit", "request_budget",
                     "CountTokens", "model_called=false", "model_calls", "contracts/tools.md#rewrite-limits-and-accounting"):
            assert term in section
        request = json.loads(re.search(r"```json\n(.*?)\n```", section, re.S).group(1))
        assert request == dict(text="The team will carry out a review of the draft.", language="en", degree="rewrite")
        assert "diagnosis" not in request
    for phrase in ("leave the text unsent", "never items or a partial document", "discards every candidate and diagnosis",
                   "No grandchild retries", "context plus background also total at most 4,000",
                   "unknown usage stays unknown", "independent judgments", "remain pending",
                   "English and Chinese rewrite quality is unverified"):
        assert phrase in EN
    for phrase in ("\u672a\u9001\u4fe1\u30fb\u672a\u51e6\u7406", "\u5019\u88dc\u3068\u8a3a\u65ad\u3092\u3059\u3079\u3066\u7834\u68c4",
                   "context\u3068\u80cc\u666f\u306e\u5408\u8a08\u30824,000", "\u672a\u691c\u8a3c", "\u672a\u5b8c\u4e86"):
        assert phrase in JA
    assert "minimal confirm" not in EN and "not implemented by this Skill yet" not in EN


def test_optional_judgment_settings_and_disclosure_match_both_languages():
    contract = (ROOT / "contracts/config.md").read_text()
    for section in (EN, JA):
        for term in ("judgment.enabled", "COPYEDITOR_JUDGMENT_ENABLED=true",
                     "judgment.enabled=false", "COPYEDITOR_JUDGMENT_ENABLED=false", "TYPESAFE_API_KEY",
                     "jev-1.13.0", "reference-gate-action-v1", "gate-verify-v1", "Vertex ADC", "OAuth",
                     "TypeSafe AI", "schema_version=3", "lint_text", "verification_rejected",
                     "contracts/config.md#judgment-fields", "contracts/tools.md#registered-threshold-classification"):
            assert term in section
        for identifier in ("jev-1.13.0", "reference-gate-action-v1", "gate-verify-v1", "TYPESAFE_API_KEY"):
            assert identifier in contract
        assert not re.search(r"TYPESAFE_API_KEY\s*[:=]", section)
    for phrase in ("Optional judgment", "Judgment defaults to off",
                   "Disabled mode does not read or require this key", "and restarting",
                   "no per-request switch", "runtime secret environment", "never config, placeholders",
                   "direct MCP calls have no guaranteed per-request consent", "operators must inform",
                   "Vertex-only permission does not cover", "neither adoption permission nor proof",
                   "retention and processing region follow its own policy", "not added to audit logs"):
        assert phrase in EN
    for phrase in ("任意の判定", "判定は既定でoff", "無効時はこのkeyを参照せず",
                   "再起動", "依頼単位の切替", "実行時のsecret環境変数だけ", "config、placeholder",
                   "依頼ごとの同意は保証しません", "運用者は有効化前", "Vertexだけへの許可は追加先を含みません",
                   "意味を保持できた証明でもありません", "保持方針と処理地域", "監査ログに追加せず"):
        assert phrase in JA

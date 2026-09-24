"""Static reference-scenario consistency; real agent compliance is owner acceptance."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from plugin_checks import check_plugin, skill_body
SKILL = ROOT / "skills/copyeditor/SKILL.md"


def scenarios(text):
    rows = {}
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        fields = [field.strip() for field in line.strip("|").split("|")]
        if fields[0] == "Situation":
            continue
        assert len(fields) == 4 and fields[0] not in rows
        condition, send, apply, outcome = fields
        assert send in ("yes", "no") and apply in ("yes", "no")
        assert not (send == "no" and apply == "yes")
        rows[condition] = (send, apply, outcome)
    return rows


@pytest.mark.parametrize("condition,outcome", [
    ("permission_missing", "ask_once"), ("permission_revoked", "unprocessed"),
    ("client_denied", "unprocessed_no_alternate_route"),
    ("inseparable_secret", "unprocessed_without_echo"),
    ("html_needs_exclusion", "unprocessed_whole_document"),
    ("rewrite_unsupported", "unprocessed_no_polish_fallback"),
    ("unknown_input_schema", "unprocessed")])
def test_ac_01_1_ac_01_2_ac_01_3_ac_01_7_ac_02_9_unsent_scenarios(condition, outcome):
    assert scenarios(SKILL.read_text())[condition] == ("no", "no", outcome)


@pytest.mark.parametrize("condition,outcome", [
    ("safe_candidate_edits_forbidden", "skipped_user_choice"),
    ("ambiguous_location", "skipped_location"),
    ("unfixable", "skipped_review"), ("rejected", "rejected_with_checks"),
    ("unchanged_valid_candidate", "adopted_unchanged"), ("text_first_truncation", "one_generation_split"),
    ("child_truncation", "unprocessed_no_grandchildren"), ("html_output_limit", "unprocessed_no_split"),
    ("request_budget", "unprocessed_no_retry"), ("malformed_response", "unprocessed")])
def test_ac_01_4_ac_01_5_ac_01_6_ac_01_8_ac_01_9_ac_01_10_ac_01_12_ac_07_4_no_write_scenarios(condition, outcome):
    assert scenarios(SKILL.read_text())[condition] == ("yes", "no", outcome)


@pytest.mark.parametrize("condition", ["safe_candidate_edits_permitted"])
def test_ac_01_5_ac_01_11_valid_current_candidates_remain_adoptable(condition):
    assert scenarios(SKILL.read_text())[condition] == ("yes", "yes", "adopted_changed")


def test_ac_01_3_privacy_respects_scope_without_custom_pattern_or_path_filters():
    text = SKILL.read_text()
    assert "Do not submit actual secrets or ranges the user excludes" in text
    assert "never echo secrets in reports" in text
    assert "HTML needing exclusion remains wholly unsent" in text
    assert "Do not add custom credential-pattern scanning or blanket local-path exclusions" in text
    assert not any(marker in text for marker in ("(?:gh", "(?:AKIA", "AIza[", "sk-[", "paths beginning"))


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_ac_01_13_ac_01_14_ac_07_1_ac_07_2_ac_07_3_ac_07_6_ac_07_12_complete_procedure_is_distributed(client):
    check_plugin(ROOT, client)
    package = ROOT / "plugins" / client / "skills/copyeditor"
    raw = (package / "SKILL.md").read_bytes()
    assert skill_body(raw) == skill_body(SKILL.read_bytes())
    assert scenarios(raw.decode()) == scenarios(SKILL.read_text())
    for target in re.findall(r"\]\(([^)]+)\)", raw.decode()):
        if target.startswith("https://github.com/taketetsu1982/copyeditor/blob/main/contracts/tools.md#"):
            continue
        resolved = (package / target.split("#", 1)[0]).resolve()
        assert resolved.is_relative_to(package) and resolved.is_file()
    assert b"disable-model-invocation: true" not in raw

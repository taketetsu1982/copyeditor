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
    ("unknown_schema", "unprocessed")])
def test_ac_01_1_ac_01_2_ac_01_3_ac_01_7_ac_02_9_unsent_scenarios(condition, outcome):
    assert scenarios(SKILL.read_text())[condition] == ("no", "no", outcome)


@pytest.mark.parametrize("condition,outcome", [
    ("safe_candidate_edits_forbidden", "skipped_user_choice"),
    ("source_changed", "skipped_source_mismatch"), ("ambiguous_location", "skipped_location"),
    ("unfixable", "skipped_review"), ("rejected", "rejected_with_checks"),
    ("meaning_uncertain", "skipped_preservation"), ("related_group_not_atomic", "skipped_related_group"),
    ("unchanged_valid_candidate", "adopted_unchanged"), ("text_first_truncation", "one_generation_split"),
    ("child_truncation", "unprocessed_no_grandchildren"), ("html_output_limit", "unprocessed_no_split"),
    ("request_budget", "unprocessed_no_retry"), ("malformed_response", "unprocessed"),
    ("no_issue_changed", "unprocessed")])
def test_ac_01_4_ac_01_5_ac_01_6_ac_01_8_ac_01_9_ac_01_10_ac_01_12_ac_07_4_no_write_scenarios(condition, outcome):
    assert scenarios(SKILL.read_text())[condition] == ("yes", "no", outcome)


@pytest.mark.parametrize("condition", ["safe_candidate_edits_permitted_atomic", "compatible_verified_legacy_candidate"])
def test_ac_01_5_ac_01_11_valid_current_and_compatible_candidates_remain_adoptable(condition):
    assert scenarios(SKILL.read_text())[condition] == ("yes", "yes", "adopted_changed")


def test_ac_01_3_minimum_secret_patterns_cover_synthetic_formats():
    expressions = re.findall(r"`([^`]+)`", SKILL.read_text())
    patterns = [re.compile(value) for value in expressions if value.startswith("(?:gh") or value.startswith("(?:AKIA")
                or value.startswith("AIza[") or value.startswith("sk-[")]
    assert len(patterns) == 4
    values = [prefix + "X" * 24 for prefix in ("ghp_", "github_pat_", "npm_", "glpat-", "xoxb-")]
    values += ["AKIA" + "X" * 16, "ASIA" + "X" * 16, "AIza" + "X" * 35, "sk-" + "X" * 24]
    assert all(any(pattern.search(value) for pattern in patterns) for value in values)
    assert not any(pattern.search("Ordinary prose without credentials.") for pattern in patterns)


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_ac_01_13_ac_01_14_ac_07_1_ac_07_2_ac_07_3_ac_07_6_ac_07_12_complete_procedure_is_distributed(client):
    check_plugin(ROOT, client)
    package = ROOT / "plugins" / client / "skills/copyeditor"
    raw = (package / "SKILL.md").read_bytes()
    assert skill_body(raw) == skill_body(SKILL.read_bytes())
    assert scenarios(raw.decode()) == scenarios(SKILL.read_text())
    for target in re.findall(r"\]\(([^)]+)\)", raw.decode()):
        resolved = (package / target.split("#", 1)[0]).resolve()
        assert resolved.is_relative_to(package) and resolved.is_file()
    assert b"disable-model-invocation: true" not in raw

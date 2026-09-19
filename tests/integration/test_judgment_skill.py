"""Static Skill scenarios and distribution checks, not real client acceptance."""
import re
from pathlib import Path

import pytest

from tests.integration.test_rewrite_skill import scenarios
from plugin_checks import check_plugin, skill_body

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "skills/copyeditor/SKILL.md"
CONTRACT = ROOT / "contracts/tools.md"


@pytest.fixture(params=[SOURCE, *(ROOT / "plugins" / client / "skills/copyeditor/SKILL.md" for client in ("claude", "codex"))])
def skill(request):
    return request.param.read_text()


@pytest.mark.parametrize("condition,outcome", [
    ("judgment_enabled_permission_missing", "ask_once_with_typesafe"),
    ("judgment_unknown_permission_missing", "ask_once_with_typesafe"),
    ("judgment_permission_denied", "unprocessed_no_alternate_route"),
    ("judgment_permission_revoked", "unprocessed_no_alternate_route"),
    ("judgment_client_denied", "unprocessed_no_alternate_route"),
    ("judgment_unsupported_schema_with_consent", "unprocessed"),
])
def test_additional_destination_or_unknown_schema_prevents_submission(skill, condition, outcome):
    assert scenarios(skill)[condition] == ("no", "no", outcome)


@pytest.mark.parametrize("condition,outcome", [
    ("judgment_insufficient", "adopted_unchanged"), ("judgment_not_run", "adopted_unchanged"),
    ("judgment_no_issue", "adopted_unchanged"),
    ("judgment_classification_mismatch", "unprocessed"), ("judgment_unknown_threshold_version", "unprocessed"),
    ("verification_rejected", "rejected_with_checks"),
    ("verification_pass_meaning_uncertain", "skipped_preservation"),
    ("related_verification_rejected", "skipped_related_group"),
])
def test_received_results_keep_nonchange_rejection_and_error_distinct(skill, condition, outcome):
    assert scenarios(skill)[condition] == ("yes", "no", outcome)


def test_discovery_checks_both_markers_and_schema_without_body_probe(skill):
    contract = CONTRACT.read_text()
    markers = re.findall(r"^copyeditor\.judgment=.*$", contract, re.M)
    assert len(markers) == 2 and all(marker in skill for marker in markers)
    for clause in ("On every request", "output schema without a body probe", "Missing, contradictory, or unknown",
                   "known v1/v2 schema alone does not prove", "Do not extend Vertex-only permission",
                   "single combined confirmation", "Consent never makes an unsupported output schema safe"):
        assert clause in skill
    assert scenarios(skill)["judgment_disabled_valid_candidate"] == ("yes", "yes", "adopted_changed")


def test_classification_uses_public_registry_without_copying_threshold_tables(skill):
    refs = re.findall(r"<https://github.com/taketetsu1982/copyeditor/blob/main/contracts/tools.md#([^>]+)>", skill)
    headings = {re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
                for heading in re.findall(r"^#+ (.+)$", CONTRACT.read_text(), re.M)}
    assert set(refs) == {"version-selection-and-compatibility", "registered-threshold-classification"}
    assert set(refs) <= headings
    section = skill.split("## Discover judgment and validate v3", 1)[1].split("## Apply only permitted local edits", 1)[0]
    assert not any(number in section for number in ("0.53", "0.20", "0.80"))
    for clause in ("finite, unrounded", "five ordered axes", "raw Choice distribution/selected/confidence",
                   "effective action/source", "Hash equality alone is insufficient", "never downgrade it to legacy",
                   "Confidence is not an adoption or fallback threshold", "do not rewrite the raw Choice"):
        assert clause in section


def test_reporting_and_related_groups_do_not_turn_nonexecution_into_rejection(skill):
    for clause in ("adopted/unchanged, without writing or causing related-item skips",
                   "diagnosis=null (not diagnosed)", "no_issue (editor found no issue)",
                   "insufficient (insufficient grounds for change)", "not as a defect in the original",
                   "preserving each member's specific reason", "Do not regenerate or switch providers",
                   "Verification pass never replaces your own meaning", "an atomic application",
                   "model_called=false does not prove no transmission", "both provider rows",
                   "including failures", "Do not turn null into zero", "Never add different currencies"):
        assert clause in skill
    examples = skill.split("Fixed reporting examples", 1)[1].split("## Apply only permitted local edits", 1)[0]
    assert examples.count('"Adopted, unchanged:') == 3
    assert "not diagnosed" in examples and "editor found no issue" in examples
    assert "discarded candidate" in examples and "local meaning and source comparisons" in examples


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_both_generated_skills_preserve_the_complete_judgment_procedure(client):
    check_plugin(ROOT, client)
    generated = ROOT / "plugins" / client / "skills/copyeditor/SKILL.md"
    assert skill_body(generated.read_bytes()) == skill_body(SOURCE.read_bytes())
    assert scenarios(generated.read_text()) == scenarios(SOURCE.read_text())

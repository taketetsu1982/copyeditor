"""Static instruction consistency, not proof of real client compliance.
AC-08-1, AC-08-3: disabled procedure and distinct non-change reports.
AC-08-5, AC-08-6: no verification regeneration or substitute for human comparison.
AC-08-10, AC-08-17, AC-08-18: accounting, per-request permission and consent limits."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from plugin_checks import check_plugin, skill_body
SOURCE = ROOT / "skills/copyeditor/SKILL.md"


@pytest.fixture(params=[SOURCE, *(ROOT / "plugins" / client / "skills/copyeditor/SKILL.md" for client in ("claude", "codex"))])
def skill(request):
    return request.param.read_text()


def test_discovery_and_permission_cover_unknown_and_refused_destinations(skill):
    for clause in ("Before each request", "judgment marker and output schema without a body probe",
                   "Enabled, missing, contradictory, or unknown", "TypeSafe AI as well as MCP and Vertex AI",
                   "body, permitted context/background, and candidates", "Do not extend Vertex-only permission",
                   "single confirmation and client approval", "Refusal means no submission or alternate route",
                   "Confirmed disabled keeps the v1/v2 procedure", "unsupported schemas remain unsent even with consent"):
        assert clause in skill


def test_public_contract_is_referenced_without_repeating_classification_rules(skill):
    refs = re.findall(r"<https://github.com/taketetsu1982/copyeditor/blob/main/contracts/tools.md#([^>]+)>", skill)
    contract = (ROOT / "contracts/tools.md").read_text()
    headings = {re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
                for heading in re.findall(r"^#+ (.+)$", contract, re.M)}
    assert set(refs) == {"judgment-payloads-and-validation", "registered-threshold-classification"}
    assert set(refs) <= headings
    section = skill.split("## Optional judgment", 1)[1].split("## Apply only permitted local edits", 1)[0]
    assert not any(value in section for value in ("0.53", "0.30", "0.70", "|", "Fixed reporting examples"))
    assert "validation fails, leave the response unprocessed" in section
    assert "never downgrade broken v3 to legacy" in section


def test_judgment_preserves_comparison_consent_and_distinct_outcomes(skill):
    for clause in ("neither adoption permission nor proof of preservation", "your own meaning comparison",
                   "the user's confirmation", "Server disclosure is not consent",
                   "users calling MCP without this Skill", "successful unchanged originals, not rejections",
                   "not diagnosed (diagnosis=null)", "editor found no issue (no_issue)", "insufficient grounds for change",
                   "rejection of the discarded candidate", "related-group rule without losing specific reasons",
                   "Do not regenerate after verification rejection", "judgment enabled/disabled/unknown",
                   "TypeSafe AI permission scope", "both providers' calls", "including failures",
                   "model_called=false does not prove no transmission"):
        assert clause in skill


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_generated_skills_match_the_canonical_procedure(client):
    check_plugin(ROOT, client)
    generated = ROOT / "plugins" / client / "skills/copyeditor/SKILL.md"
    assert skill_body(generated.read_bytes()) == skill_body(SOURCE.read_bytes())

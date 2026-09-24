"""Static instruction consistency, not proof of real client compliance.
AC-08-1, AC-08-3: disabled procedure and distinct non-change reports.
AC-08-5, AC-08-6: internal retry signals never grant adoption permission.
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
    for clause in ("Fetch the connected tool definition for every request without a body probe",
                   "single judgment destination marker that is known and noncontradictory",
                   "unknown tool name, missing or unknown input schema",
                   "missing, contradictory, or unknown marker leaves the body unsent",
                   "withdrawal or \"do not send\" overrides earlier permission", "normal approval flow",
                   "do not retry through another tool, provider, route, or relaxed approval setting",
                   "Do not add a separate per-request TypeSafe confirmation",
                   "Unknown input schemas or destination markers remain unsent",
                   "Require explicit rewrite support in the degree enum",
                   "reauthentication alone is not a refresh", "matching legacy Skill, not a fallback"):
        assert clause in skill
    preflight = skill.split("## Extract and discover before sending", 1)[1].split("## Validate the complete result", 1)[0]
    assert "complete generation-4 output schema" not in preflight
    assert "Do not block submission because the host hides the output schema" in preflight
    assert "description's statements about regeneration, candidate guarantees, or permission" in preflight
    assert "destination and permission checks still apply" in preflight


def test_public_contract_is_referenced_without_repeating_classification_rules(skill):
    refs = re.findall(r"https://github.com/taketetsu1982/copyeditor/blob/main/contracts/tools.md#([a-z-]+)", skill)
    contract = (ROOT / "contracts/tools.md").read_text()
    headings = {re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
                for heading in re.findall(r"^#+ (.+)$", contract, re.M)}
    assert set(refs) == {"current-version-selection", "current-edit-payloads"}
    assert set(refs) <= headings
    section = skill.split("## Validate the complete result", 1)[1].split("## Apply only permitted local edits", 1)[0]
    assert not any(value in section for value in ("0.53", "0.30", "0.70", "|", "Fixed reporting examples"))
    assert "Select the response schema by (invoked tool name, schema_version); require schema_version=4" in section
    assert "A malformed response, unsupported generation, or mode mismatch is unprocessed as invalid_response" in section
    assert "never a legacy fallback or partial success" in section
    assert "complete generation-4 shape, envelope/content equality, status, expected judgment mode" in section
    assert "all original IDs exactly once in original order" in section


def test_judgment_preserves_comparison_consent_and_distinct_outcomes(skill):
    for clause in ("does not grant editing permission", "server disclosure creates permission",
                   "enabled TypeSafe AI processing", "successful unchanged original, not a rejection",
                   "detection-exempt items have null diagnosis", "final generated candidate can remain",
                   "at most one server retry per item", "Verification results are internal retry signals",
                   "Do not add a separate semantic comparison", "related-item cascade",
                   "A returned `rejected` flag leaves the original unchanged",
                   "provider-specific", "including failures and unknown amounts",
                   "Zero model calls does not prove zero transmission", "CountTokens may already have sent data"):
        assert clause in skill


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_generated_skills_match_the_canonical_procedure(client):
    check_plugin(ROOT, client)
    generated = ROOT / "plugins" / client / "skills/copyeditor/SKILL.md"
    assert skill_body(generated.read_bytes()) == skill_body(SOURCE.read_bytes())

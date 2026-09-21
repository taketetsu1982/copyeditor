"""Default style extraction and request-local editing tone; no naturalness claim."""
import json
from pathlib import Path

import pytest

from copyeditor.config import ConfigError
from copyeditor.judgment import JudgmentBlock, JudgmentInput
from copyeditor.judgment_v2_batch import prepare_judgments
from copyeditor.prompt import edit_contents
from copyeditor.providers.base import Background, EditGenerationInput, SourceItem
from copyeditor.rules import default_style, effective_tone, load_rules


def raw(style="Natural expression.", section="Context weights"):
    source = Path("rules/ja.md").read_text()
    return source.replace("## " + section + "\n", "## " + section + "\nDefault style: " + style + "\n").encode()


def test_new_marker_is_optional_for_legacy_loader_but_required_by_v4():
    assert "ja" in load_rules(Path("rules"), None).languages
    with pytest.raises(ConfigError): default_style(Path("rules/ja.md").read_bytes(), "ja")
    assert default_style(raw(), "ja") == "Natural expression."
    assert default_style(raw("\u8a9e" * 1000), "ja") == "\u8a9e" * 1000


@pytest.mark.parametrize("document,overlay", [(raw(""), False), (raw("\u3000"), False),
    (raw("x" * 1001), False), (raw(section="Vocabulary"), False),
    (raw().replace(b"Default style: ", b"Default style: Duplicate.\nDefault style: "), False), (raw(), True)])
def test_missing_empty_misplaced_duplicate_and_overlay_markers_fail(document, overlay):
    with pytest.raises(ConfigError) as error: default_style(document, "ja", overlay=overlay)
    assert (error.value.code, error.value.field) == ("invalid_rules", "rules")


@pytest.mark.parametrize("tone", ["", "\t\u3000", "  Explicit style.  "])
def test_frozen_effective_tone_reaches_initial_retry_but_never_judgment(tone):
    selected = effective_tone(tone, default_style(raw(), "ja"))
    assert selected == (tone if tone.strip() else "Natural expression.")
    background = Background("", "", selected, "")
    generation = EditGenerationInput((SourceItem("a", "body", ""), SourceItem("b", "other", "")), "ja", "text", background, "policy")
    retry = generation._replace(items=generation.items[:1])
    initial, repeated = (json.loads(edit_contents(value)) for value in (generation, retry))
    assert len(initial["items"]) == 2 and len(repeated["items"]) == 1
    assert initial["background"] == repeated["background"] == background._asdict()
    assert selected in edit_contents(generation)
    data = JudgmentInput("detect", "ja", "text", background, selected, (JudgmentBlock(1, "body", "", None, None),))
    assert selected.encode() not in prepare_judgments(data).requests[0]

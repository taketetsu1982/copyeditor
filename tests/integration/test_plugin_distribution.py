import shutil

import pytest

from tests.integration.test_plugin_codex import checkout, base_checkout
from tests.integration.test_phase1 import suite, run
from tests.conftest import PHASE2_MODULES
from plugin_checks import check_plugin


@pytest.mark.parametrize("missing", [None, "claude", "codex"])
def test_ac_06_1_ac_06_2_ac_06_3_ctr02_both_packages_required(checkout, missing):
    if missing:
        shutil.rmtree(checkout / "plugins" / missing)
    def check_both():
        for client in ("claude", "codex"):
            check_plugin(checkout, client)
    if missing:
        with pytest.raises(ValueError):
            check_both()
    else:
        check_both()


@pytest.mark.parametrize("missing", [None, *sorted(PHASE2_MODULES)])
def test_ac_06_1_ac_06_2_ctr02_distribution_modules_required(suite, missing):
    if missing:
        (suite / missing).unlink()
    result = run(suite, "tests")
    assert result.returncode == (1 if missing else 0), result.stdout + result.stderr
    assert ("CONTRACTS FAIL" if missing else "CONTRACTS PASS") in result.stdout
    assert (f"Missing Phase 2 module: {missing}" in result.stdout) == bool(missing)


def test_ac_07_3_ac_07_6_ctr02_common_update_is_atomic_across_packages(checkout):
    import json
    from build_plugins import build
    from copyeditor.rules import load_rules
    from plugin_checks import GENERATED, SKILL

    initial = load_rules(checkout / "rules", None)
    fixed = {path: path.read_bytes() for client in ("claude", "codex")
             for path in (checkout / "plugins" / client / ".mcp.json",
                          checkout / "plugins" / client / f".{client}-plugin/plugin.json")}
    common = checkout / "rules/common.md"
    common.write_bytes(common.read_bytes() + b"\nSynthetic version drift.\n")
    updated = load_rules(checkout / "rules", None)
    assert updated.common_version != initial.common_version
    for client in ("claude", "codex"):
        with pytest.raises(ValueError):
            check_plugin(checkout, client)
        build(checkout, client)
        build(checkout, client, check=True)
        package = checkout / "plugins" / client
        versions = json.loads((package / SKILL / "rules-version.json").read_text())
        assert versions == {"common_version": updated.common_version, "rules_version": updated.rules_version}
        assert (package / SKILL / "rules/common.md").read_bytes() == common.read_bytes()
        generated = {path: (package / path).read_bytes() for path in GENERATED}
        build(checkout, client)
        assert generated == {path: (package / path).read_bytes() for path in GENERATED}
    assert all(path.read_bytes() == raw for path, raw in fixed.items())


def test_ac_08_11_ac_08_18_version_030_matches_server_and_both_plugins():
    import json
    import tomllib
    from pathlib import Path
    from copyeditor.config import load_config

    root = Path(__file__).resolve().parents[2]
    versions = [tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]]
    for client in ("claude", "codex"):
        check_plugin(root, client)
        versions.append(json.loads((root / f"plugins/{client}/.{client}-plugin/plugin.json").read_text())["version"])
    assert versions == ["0.3.0"] * 3
    assert load_config(root / "config.example.yaml", {"GOOGLE_CLOUD_PROJECT": "fixture"})["judgment.enabled"] is False
    en, ja = (root / "README.md").read_text().split("## 日本語\n")
    for section in (en, ja):
        for term in ("Version 0.3.0", "judgment.enabled=false", "schema_version=4"):
            assert term in section
    assert all(term in en for term in ("matching Skill", "calibration, native review and real-client quality acceptance are pending",
                                       "Offline CI does not establish quality", "Tagging/publication remain separate owner operations"))
    assert all(term in ja for term in ("対応Skill", "校正・native確認・実client品質受入は未完了",
                                       "offline CIは品質を証明しません", "tag・公開は所有者の別操作"))

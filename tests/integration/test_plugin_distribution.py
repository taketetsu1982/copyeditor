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

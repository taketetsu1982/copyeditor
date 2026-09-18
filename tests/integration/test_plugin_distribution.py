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

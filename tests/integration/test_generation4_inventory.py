"""Current HTTP guarantees cannot disappear while mandatory CI remains green."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import GENERATION4_MODULES, Phase1Contracts, judgment_fingerprint, phase1_inventory
from tests.integration.test_phase1 import collected_contracts

ROOT = Path(__file__).resolve().parents[2]


def test_current_http_modules_have_nonempty_fixed_pins(collected_contracts):
    inventory = phase1_inventory(ROOT)
    for module, expected in GENERATION4_MODULES.items():
        assert expected[0] > 0
        assert judgment_fingerprint(ROOT, module, collected_contracts.items) == expected
        assert inventory[module + "::*"] == {expected[1]: {"count": expected[0]}}


@pytest.mark.parametrize("module", sorted(GENERATION4_MODULES))
@pytest.mark.parametrize("change", ["module", "case", "replacement"])
def test_missing_or_replaced_generation_four_consumer_fails(collected_contracts, module, change):
    items = list(collected_contracts.items)
    selected = next(item for item in items if item.nodeid.split("::")[0] == module)
    if change == "module":
        items = [item for item in items if item.nodeid.split("::")[0] != module]
    elif change == "case":
        items.remove(selected)
    else:
        items[items.index(selected)] = SimpleNamespace(nodeid=selected.nodeid,
            callspec=SimpleNamespace(params={"replacement": True}))
    assert judgment_fingerprint(ROOT, module, items) != GENERATION4_MODULES[module]
    if change != "replacement":
        gate = Phase1Contracts(collected_contracts.config)
        gate.pytest_collection_finish(SimpleNamespace(items=items))
        assert "Inventory mismatch: " + module + "::*" in gate.errors

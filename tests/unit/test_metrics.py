from decimal import Decimal
from types import SimpleNamespace

import pytest

from copyeditor.config import load_config
from copyeditor.metrics import Metrics
from copyeditor.providers.base import ProviderFailure, Usage
from copyeditor.providers.vertex import usage as vertex_usage


def metrics(pricing=None, model="model", elapsed=0):
    return Metrics(0, model, pricing or {}, clock=lambda: elapsed)


def test_ctr01_no_call_and_pending_failure():
    meter = metrics()
    assert meter.snapshot() == {
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "cost": None, "latency_ms": 0, "model_calls": 0, "regeneration_attempted": False,
    }
    meter.start_call()
    assert meter.snapshot()["usage"] == dict.fromkeys(Usage._fields)
    assert meter.snapshot()["model_calls"] == 1
    assert meter.snapshot()["cost"] is None


@pytest.mark.parametrize("missing", [None, 0, 1, 2])
def test_ac_02_3_failure_usage_and_component_null(missing):
    meter = metrics()
    first = [10, 20, 80]
    if missing is not None:
        first[missing] = None
    failure = ProviderFailure("provider_error", Usage(*first))
    meter.record_usage(meter.start_call(), failure.usage)
    meter.record_usage(meter.start_call(), Usage(1, 2, 9))
    expected = [11, 22, 89]
    if missing is not None:
        expected[missing] = None
    assert meter.snapshot()["usage"] == Usage(*expected)._asdict()
    assert meter.snapshot()["model_calls"] == 2
    assert meter.snapshot()["regeneration_attempted"] is True
    with pytest.raises(ValueError):
        meter.record_usage(0, Usage(0, 0, 0))
    with pytest.raises(ValueError):
        meter.start_call()


@pytest.mark.parametrize("tokens,expected", [
    (Usage(1, 0, None), "0.000001"), (Usage(0, 0, 0), "0.000000"),
    (Usage(2, 3, 99), "0.000007"), (Usage(None, 1, 1), None),
    (Usage(1, None, 1), None),
])
def test_ctr01_decimal_cost_from_resolved_exact_price(tmp_path, tokens, expected):
    config = load_config(tmp_path / "absent", {"GOOGLE_CLOUD_PROJECT": "test-project", "COPYEDITOR_PRICING":
        '{"model":{"currency":"USD","input_per_million":0.5,"output_per_million":2},'
        '"other":{"currency":"JPY","input_per_million":100,"output_per_million":100}}'})
    meter = metrics(config["pricing"])
    assert meter.snapshot()["cost"] is None
    meter.record_usage(meter.start_call(), tokens)
    assert meter.snapshot()["cost"] == (None if expected is None else {
        "amount": expected, "currency": "USD"})
    assert metrics(config["pricing"], "model-alias").price is None


def test_ac_02_3_thinking_usage_is_counted_once_and_cost_aggregates_before_rounding():
    meter = metrics({"model": {"currency": "EUR", "input_per_million": Decimal("0.25"),
                               "output_per_million": Decimal("0")}})
    response = SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=1, candidates_token_count=2, thoughts_token_count=3, total_token_count=8))
    for _ in range(2):
        meter.record_usage(meter.start_call(), vertex_usage(response))
    assert meter.snapshot()["usage"] == Usage(2, 10, 16)._asdict()
    assert meter.snapshot()["cost"] == {"amount": "0.000001", "currency": "EUR"}


@pytest.mark.parametrize("elapsed,expected", [(0, 0), (0.00049, 0), (0.00051, 1), (1.2346, 1235)])
def test_ctr01_monotonic_latency_and_request_isolation(elapsed, expected):
    meter = metrics(elapsed=elapsed)
    assert meter.snapshot()["latency_ms"] == expected
    meter.start_call()
    snapshot = meter.snapshot()
    snapshot["usage"]["input_tokens"] = 100
    assert meter.snapshot()["usage"]["input_tokens"] is None
    assert metrics().snapshot()["model_calls"] == 0


def test_ctr01_unreturned_retry_and_missing_thinking_remain_unknown():
    meter = metrics()
    meter.record_usage(meter.start_call(), Usage(1, 2, 3))
    retry = meter.start_call()
    assert meter.snapshot()["usage"] == dict.fromkeys(Usage._fields)
    response = SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=4, candidates_token_count=5, total_token_count=9))
    meter.record_usage(retry, vertex_usage(response))
    assert meter.snapshot()["usage"] == Usage(5, None, 12)._asdict()
    assert meter.snapshot()["regeneration_attempted"] is True


def test_ctr01_elapsed_starts_at_tool_entry_and_includes_final_validation():
    now = [10.0]
    meter = Metrics(9.0, "model", {}, clock=lambda: now[0])
    meter.record_usage(meter.start_call(), Usage(0, 0, 0))
    assert meter.snapshot()["latency_ms"] == 1000
    now[0] = 11.0
    assert meter.snapshot()["latency_ms"] == 2000


def test_ac_07_7_ctr01_diagnosis_and_later_batches_are_not_regeneration():
    meter = Metrics(0, "model", {}, clock=lambda: 1, degree="rewrite")
    for _ in range(9):
        meter.record_usage(meter.start_call(is_regeneration=False), Usage(2, 3, 5))
    assert meter.snapshot()["model_calls"] == 9
    assert meter.snapshot()["regeneration_attempted"] is False
    assert meter.snapshot()["usage"] == Usage(18, 27, 45)._asdict()
    meter.start_call(is_regeneration=True)
    assert meter.snapshot()["regeneration_attempted"] is True
    assert meter.snapshot()["usage"] == dict.fromkeys(Usage._fields)
    meter.record_usage(meter.start_call(is_regeneration=False), Usage(1, 2, 3))
    assert meter.snapshot()["regeneration_attempted"] is True


def test_ac_07_7_ctr01_maximum_started_calls_and_failure_slots_are_retained():
    meter = Metrics(0, "model", {}, degree="rewrite")
    for i in range(17):
        assert meter.start_call(is_regeneration=i > 8) == i
    before = meter.snapshot()
    assert before["model_calls"] == 17
    assert before["usage"] == dict.fromkeys(Usage._fields)
    assert before["cost"] is None
    with pytest.raises(ValueError):
        meter.start_call(is_regeneration=False)
    after = meter.snapshot()
    assert after["model_calls"] == before["model_calls"]
    assert after["regeneration_attempted"] is True


@pytest.mark.parametrize("marker", [None, 0, 1, "true"])
def test_inv11_rewrite_requires_explicit_boolean_before_start(marker):
    meter = Metrics(0, "model", {}, degree="rewrite")
    with pytest.raises(ValueError):
        meter.start_call(is_regeneration=marker)
    assert meter.snapshot()["model_calls"] == 0
    assert meter.snapshot()["regeneration_attempted"] is False


@pytest.mark.parametrize("missing", [None, 0, 1, 2])
def test_inv11_all_rewrite_stages_preserve_partial_usage_and_actual_cost(missing):
    prices = {"model": {"input_per_million": 1, "output_per_million": 2, "currency": "USD"}}
    meter = Metrics(0, "model", prices, degree="rewrite")
    for regeneration in (False, False, True, False):
        values = [10, 20, 35]
        if missing is not None:
            values[missing] = None
        meter.record_usage(meter.start_call(is_regeneration=regeneration), Usage(*values))
    values = [40, 80, 140]
    if missing is not None:
        values[missing] = None
    result = meter.snapshot()
    assert result["usage"] == Usage(*values)._asdict()
    assert result["cost"] == (None if missing in (0, 1) else {"amount": "0.000200", "currency": "USD"})
    assert result["model_calls"] == 4 and result["regeneration_attempted"] is True


def test_ctr01_legacy_defaults_and_explicit_polish_marker():
    for degree in ("polish", "rewrite"):
        meter = Metrics(0, "model", {}, degree=degree)
        meter.start_call(is_regeneration=False)
        meter.start_call(is_regeneration=False)
        assert meter.snapshot()["regeneration_attempted"] is False
    with pytest.raises(ValueError):
        Metrics(0, "model", {}, degree="other")

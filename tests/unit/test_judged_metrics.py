import asyncio
from types import SimpleNamespace

import pytest

from copyeditor.judged_metrics import JudgedMetrics
from copyeditor.providers.base import Usage


def price(input_rate=0.5, output_rate=0, currency="USD"):
    return dict(input_per_million=input_rate, output_per_million=output_rate, currency=currency)


def meter(pricing=None, **kwargs):
    return JudgedMetrics(0, "editor", pricing or {}, **kwargs)


def record(m, role, usage):
    with m.call(role) as slot:
        m.record_usage(role, slot, usage)


def test_zero_calls_keep_both_rows_and_no_cost():
    result = meter(clock=lambda: 2).snapshot()
    assert result["cost"] is None and result["model_called"] is False
    assert result["latency_ms"] == 2000
    assert result["regeneration_attempted"] is False
    for row, role, provider, model in zip(result["providers"], ("editing", "judgment"),
                                        ("vertex", "typesafe"), ("editor", "jev-1.13.0")):
        assert row == dict(role=role, provider=provider, model=model, model_calls=0,
                           estimation_calls=0, usage=Usage(0, 0, 0)._asdict(), cost=None, latency_ms=0)


@pytest.mark.parametrize("editing,judgment,estimation,expected", [
    (0, 0, 0, False), (0, 1, 0, True), (0, 1, 1, True), (1, 1, 1, True), (0, 0, 1, False),
])
def test_model_called_uses_both_model_rows_only(editing, judgment, estimation, expected):
    m = meter()
    for role, count in (("editing", editing), ("judgment", judgment)):
        for _ in range(count):
            with m.call(role):
                pass
    for _ in range(estimation):
        with m.call("editing", estimation=True):
            pass
    result = m.snapshot()
    assert result["model_called"] is expected
    assert [row["model_calls"] for row in result["providers"]] == [editing, judgment]
    assert [row["estimation_calls"] for row in result["providers"]] == [estimation, 0]


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
def test_failed_waits_retain_slots_and_duration_without_local_time(error):
    now = [1.0]
    m = meter(clock=lambda: now[0])
    with m.call("editing", estimation=True):
        now[0] = 1.25
    now[0] = 2
    with pytest.raises(error):
        with m.call("judgment"):
            now[0] = 2.5
            raise error()
    now[0] = 4
    result = m.snapshot()
    assert result["latency_ms"] == 4000
    editing, judgment = result["providers"]
    assert editing["latency_ms"] == 250 and judgment["latency_ms"] == 500
    assert editing["usage"] == Usage(0, 0, 0)._asdict()
    assert judgment["usage"] == Usage(None, None, None)._asdict()
    assert result["model_called"] and result["cost"] is None


def test_component_unknown_free_output_and_no_invented_total():
    m = meter({"jev-1.13.0": price()})
    record(m, "judgment", Usage(1, 40, None))
    record(m, "judgment", Usage(2, 60, None))
    row = m.snapshot()["providers"][1]
    assert row["usage"] == Usage(3, 100, None)._asdict()
    assert row["cost"] == {"amount": "0.000002", "currency": "USD"}
    assert m.snapshot()["cost"] == row["cost"]
    record(m, "judgment", SimpleNamespace(input_tokens=True, output_tokens=-1, total_tokens="3"))
    assert m.snapshot()["providers"][1]["usage"] == Usage(None, None, None)._asdict()
    assert m.snapshot()["cost"] is None


@pytest.mark.parametrize("other,expected", [(price(), "0.000001"), (price(currency="JPY"), None), (None, None)])
def test_total_rounds_unrounded_costs_once_and_requires_same_known_currency(other, expected):
    pricing = {"editor": price(), "alias": price()}
    if other is not None:
        pricing["jev-1.13.0"] = other
    m = meter(pricing)
    for role in ("editing", "judgment"):
        record(m, role, Usage(1, 0, 1))
    result = m.snapshot()
    assert result["providers"][0]["cost"]["amount"] == "0.000001"
    assert result["cost"] == (None if expected is None else {"amount": expected, "currency": "USD"})


def test_limits_regeneration_and_usage_recording_are_independent():
    m = meter(degree="rewrite")
    for _ in range(64):
        record(m, "judgment", Usage(0, 0, 0))
    assert m.snapshot()["regeneration_attempted"] is False
    with pytest.raises(ValueError):
        with m.call("judgment"):
            pytest.fail("Exceeded judgment limit")
    for index in range(17):
        with m.call("editing", is_regeneration=index == 16) as slot:
            m.record_usage("editing", slot, Usage(1, 0, 1))
    assert m.snapshot()["regeneration_attempted"] is True
    for role, options in (("editing", {}), ("judgment", {"estimation": True})):
        with pytest.raises(ValueError):
            with m.call(role, **options):
                pytest.fail("Invalid call accepted")
    with pytest.raises(ValueError):
        m.record_usage("editing", 0, Usage(1, 0, 1))


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_failure_usage_keeps_known_components_and_does_not_hide_failure(missing):
    m = meter({"editor": price(1, 2)})
    values = [2, 3, 8]
    values[missing] = None
    with pytest.raises(RuntimeError, match="terminal"):
        with m.call("editing") as slot:
            m.record_usage("editing", slot, Usage(*values))
            raise RuntimeError("terminal")
    record(m, "editing", Usage(1, 1, 4))
    expected = [3, 4, 12]
    expected[missing] = None
    row = m.snapshot()["providers"][0]
    assert row["usage"] == Usage(*expected)._asdict()
    assert row["cost"] == ({"amount": "0.000011", "currency": "USD"} if missing == 2 else None)

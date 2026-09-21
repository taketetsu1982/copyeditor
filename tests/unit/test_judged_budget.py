"""AC-08-12: finite phase admission, calls, token/money reservations and deadlines.
Observe refusal, cancellation and exact budget boundaries, not live latency."""
import asyncio

import pytest

from copyeditor.judged_budget import JudgedBudget
from copyeditor.judged_metrics import JudgedMetrics
from copyeditor.judgment import JudgmentBlock, JudgmentInput, POLICY_ID
from copyeditor.providers.base import Background, Usage
from copyeditor.rewrite_budget import RewriteFailure


def budget(degree="polish", pricing=None, **changes):
    now = [10.0]
    config = dict(policy_version=POLICY_ID, timeout_ms=10000, polish_deadline_ms=120000,
                  rewrite_deadline_ms=240000, max_calls=64, input_budget=262144)
    config.update(changes)
    metrics = JudgedMetrics(10, "editor", pricing or {}, clock=lambda: now[0], degree=degree)
    return JudgedBudget(metrics, {"judgment." + k: v for k, v in config.items()}), now


def data(count=1, phase="detect"):
    return JudgmentInput(phase, "ja", "text", Background("", "", "", ""), "",
        tuple(JudgmentBlock(i, "body", "", "candidate", "simplify_phrasing") for i in range(1, count + 1)))


def price(rate, currency="USD"):
    return dict(input_per_million=rate, output_per_million=0, currency=currency)


def test_phase_admission_is_atomic_and_separate_from_started_calls():
    ledger, _ = budget(max_calls=1)
    with pytest.raises(RewriteFailure, match="request_budget"):
        ledger.plan(data(20))
    assert not ledger.judgment_reservations and not ledger.metrics.snapshot()["model_called"]
    ledger, _ = budget()
    prepared = ledger.plan(data(20))
    assert len(prepared.requests) > 1
    assert len(ledger.judgment_reservations) == len(prepared.requests)
    assert not ledger.metrics.snapshot()["model_called"]
    for _ in prepared.requests:
        with ledger.call("judgment") as slot:
            ledger.record_usage("judgment", slot, Usage(0, 1, None))
        ledger.checkpoint()
    assert len(ledger.judgment_reservations) == len(prepared.requests)
    with pytest.raises(RewriteFailure, match="request_budget"):
        with ledger.call("judgment"):
            pytest.fail("Unplanned call started")


def test_judgment_input_boundary_and_cumulative_phase_limit():
    original, _ = budget()
    units = original.plan(data()).plan.batches[0].input_units
    ledger, _ = budget(input_budget=units)
    ledger.plan(data())
    with pytest.raises(RewriteFailure, match="request_budget"):
        ledger.plan(data(phase="verify"))
    ledger, _ = budget(input_budget=units - 1)
    with pytest.raises(RewriteFailure, match="request_budget"):
        ledger.plan(data())
    assert not ledger.judgment_reservations


@pytest.mark.parametrize("degree,limit", [("polish", 2), ("rewrite", 17)])
def test_editing_call_and_token_ledgers_retain_reservations(degree, limit):
    ledger, _ = budget(degree)
    ledger.plan(data())
    for index in range(limit):
        with ledger.call("editing", estimated_input=0, is_regeneration=index > 0) as slot:
            ledger.record_usage("editing", slot, Usage(0, 0, 0))
    assert ledger.reservations == [(1024, 8192)] * limit
    assert sum(r[1] for r in ledger.reservations) == (16384 if limit == 2 else 139264)
    with pytest.raises(RewriteFailure, match="request_budget"):
        with ledger.call("editing", estimated_input=0):
            pytest.fail("Exceeded call cap")
    assert len(ledger.metrics.meters["editing"].calls) == limit


@pytest.mark.parametrize("missing,currency,refused", [(False, "USD", True), (False, "JPY", False), (True, "USD", False)])
def test_money_is_shared_only_for_known_same_currency_prices(missing, currency, refused):
    prices = {"editor": price(1)}
    if not missing:
        prices["jev-1.13.0"] = price(100, currency)
    ledger, _ = budget(pricing=prices)
    if refused:
        with pytest.raises(RewriteFailure, match="request_budget"):
            ledger.plan(data())
        assert not ledger.judgment_reservations
    else:
        ledger.plan(data())
        with ledger.call("editing", estimated_input=208896):
            pass
        assert ledger.reservations[0][0] == 262144


@pytest.mark.parametrize("finish,error,size,html,expected", [
    ("truncated", "invalid_response", True, True, "generation_truncated"),
    ("stop", "invalid_response", True, True, "invalid_response"),
    ("stop", None, True, True, "output_limit"), ("stop", None, False, True, "html_structure"),
    ("stop", None, False, False, "request_budget")])
def test_usage_recording_defers_overrun_until_error_precedence(finish, error, size, html, expected):
    ledger, now = budget()
    ledger.plan(data())
    with ledger.call("judgment") as slot:
        ledger.record_usage("judgment", slot, Usage(999999, 10, None))
    assert ledger.terminal_code is None
    with pytest.raises(RewriteFailure, match=expected):
        ledger.checkpoint(finish=finish, validation_error=error, output_limit=size, html_structure=html)
    now[0] = 999
    with pytest.raises(RewriteFailure, match=expected):
        with ledger.call("editing", estimated_input=0):
            pytest.fail("Terminal call started")
    assert ledger.metrics.snapshot()["providers"][1]["usage"]["input_tokens"] == 999999


def test_deadline_provider_error_estimation_and_cancel_are_monotonic():
    ledger, now = budget(polish_deadline_ms=1000, timeout_ms=60000)
    assert ledger.timeout("judgment") == ledger.timeout("editing") == 1
    with ledger.call("editing", estimation=True):
        now[0] += 0.25
    assert not ledger.metrics.snapshot()["model_called"]
    assert ledger.metrics.snapshot()["providers"][0]["latency_ms"] == 250
    now[0] = 11
    with pytest.raises(RewriteFailure, match="provider_timeout"):
        ledger.checkpoint(provider_error="provider_error", finish="truncated")
    ledger, _ = budget()
    with pytest.raises(RewriteFailure, match="provider_error"):
        ledger.checkpoint(provider_error="provider_error", validation_error="invalid_response")
    ledger, _ = budget()
    with pytest.raises(asyncio.CancelledError):
        with ledger.call("editing", estimation=True):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        ledger.plan(data())
    assert ledger.metrics.estimations["editing"] == 1


@pytest.mark.parametrize("rate,admitted", [(0.125, True), (0.125001, False)])
def test_combined_money_accepts_exact_ceiling_and_refuses_one_price_unit_above(rate, admitted):
    prices = {"editor": dict(currency="USD", input_per_million=0, output_per_million=1),
              "jev-1.13.0": dict(currency="USD", input_per_million=0, output_per_million=rate)}
    ledger, _ = budget(pricing=prices)
    ledger.plan(data())
    if admitted:
        with ledger.call("editing", estimated_input=0) as slot:
            ledger.record_usage("editing", slot, Usage(0, 0, 0))
        with pytest.raises(RewriteFailure, match="request_budget"):
            ledger.plan(data(phase="verify"))
        assert len(ledger.reservations) == len(ledger.judgment_reservations) == 1
    else:
        with pytest.raises(RewriteFailure, match="request_budget"):
            with ledger.call("editing", estimated_input=0):
                pytest.fail("Money ceiling exceeded")
        assert not ledger.reservations and not ledger.metrics.snapshot()["model_called"]


def edit_budget(degree="polish", enabled=True, pricing=None, **changes):
    from copyeditor.judged_budget import EditBudget
    from copyeditor.judged_metrics import EditMetrics
    from copyeditor.judgment_v2 import POLICY_ID as V2_POLICY
    old, now = budget(degree, **changes)
    config = dict(old.config, **{"judgment.policy_version": V2_POLICY})
    metrics = EditMetrics(10, "editor", pricing or {}, clock=lambda: now[0],
                          degree=degree, judgment_enabled=enabled)
    return EditBudget(metrics, config), now


@pytest.mark.parametrize("degree,limit", [("polish", 2), ("rewrite", 16)])
@pytest.mark.parametrize("enabled", [False, True])
def test_v4_generation_and_estimation_caps_keep_all_reservations(degree, limit, enabled):
    ledger, _ = edit_budget(degree, enabled)
    for index in range(limit):
        with ledger.call("editing", estimation=True):
            pass
        with ledger.call("editing", estimated_input=0, is_regeneration=index >= limit // 2) as slot:
            ledger.record_usage("editing", slot, Usage(0, 0, 0))
    assert ledger.reservations == [(1024, 8192)] * limit
    assert ledger.metrics.estimations["editing"] == limit
    assert sum(r[1] for r in ledger.reservations) == limit * 8192
    with pytest.raises(RewriteFailure, match="request_budget"):
        with ledger.call("editing", estimated_input=0):
            pytest.fail("Extra generation started")
    ledger, _ = edit_budget(degree, enabled)
    for _ in range(limit):
        with ledger.call("editing", estimation=True):
            pass
    with pytest.raises(RewriteFailure, match="request_budget"):
        with ledger.call("editing", estimation=True):
            pytest.fail("Extra estimation started")
    assert not ledger.metrics.snapshot()["model_called"]


def test_v4_three_phases_use_real_payloads_and_atomic_cumulative_admission():
    import json
    ledger, _ = edit_budget(max_calls=3)
    for round_ in range(3):
        prepared = ledger.plan(data(phase="detect" if round_ == 0 else "verify"), candidate_round=round_)
        assert prepared.plan.candidate_round == round_
        assert all(q["type"] == "Noul" for q in json.loads(prepared.requests[0])["questions"].values())
        with ledger.call("judgment") as slot:
            ledger.record_usage("judgment", slot, Usage(0, 0, None))
    assert len(ledger.judgment_reservations) == 3
    with pytest.raises(ValueError):
        ledger.plan(data())
    ledger, _ = edit_budget(max_calls=1)
    ledger.plan(data())
    with pytest.raises(RewriteFailure, match="request_budget"):
        ledger.plan(data(phase="verify"), candidate_round=1)
    assert len(ledger.judgment_reservations) == 1 and not ledger.metrics.snapshot()["model_called"]
    ledger, _ = edit_budget()
    for round_ in range(3):
        assert ledger.plan(data(0, "detect" if round_ == 0 else "verify"), candidate_round=round_).requests == ()
    assert not ledger.judgment_reservations


@pytest.mark.parametrize("degree,rate", [("polish", 0.25), ("rewrite", 2)])
def test_v4_money_ceiling_uses_current_output_cap_without_refunds(degree, rate):
    prices = {"editor": dict(currency="USD", input_per_million=0, output_per_million=1),
              "jev-1.13.0": dict(currency="USD", input_per_million=0, output_per_million=rate)}
    ledger, _ = edit_budget(degree, pricing=prices)
    ledger.plan(data())
    with ledger.call("judgment") as slot:
        ledger.record_usage("judgment", slot, Usage(0, 0, 0))
    with pytest.raises(RewriteFailure, match="request_budget"):
        with ledger.call("editing", estimated_input=0):
            pytest.fail("Judgment reservation was refunded")
    assert not ledger.reservations and len(ledger.judgment_reservations) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_v4_fake_clock_failure_priority_and_cancel_stop_future_calls(enabled):
    ledger, now = edit_budget(enabled=enabled)
    assert ledger.timeout("editing") == 60
    with ledger.call("editing", estimated_input=0) as slot:
        ledger.record_usage("editing", slot, Usage(1025, None, None))
    with pytest.raises(RewriteFailure, match="invalid_response"):
        ledger.checkpoint(validation_error="invalid_response")
    with pytest.raises(RewriteFailure, match="invalid_response"):
        with ledger.call("editing", estimation=True):
            pytest.fail("Terminal estimation started")
    ledger, now = edit_budget(enabled=enabled)
    now[0] += 120
    with pytest.raises(RewriteFailure, match="provider_timeout"):
        ledger.checkpoint(provider_error="provider_error")
    ledger, _ = edit_budget(enabled=enabled)
    with pytest.raises(asyncio.CancelledError):
        with ledger.call("editing", estimated_input=0):
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        with ledger.call("editing", estimation=True):
            pytest.fail("Post-cancel call started")
    assert ledger.metrics.snapshot()["providers"][0]["usage"] == Usage(None, None, None)._asdict()
    assert len(ledger.reservations) == 1


def test_v4_input_phase_boundary_and_disabled_judgment_refusal():
    ledger, _ = edit_budget()
    units = ledger.plan(data()).plan.batches[0].input_units
    ledger, _ = edit_budget(input_budget=units)
    ledger.plan(data())
    assert ledger.judgment_reservations == [(units, 65536)]
    ledger, _ = edit_budget(input_budget=units - 1)
    with pytest.raises(RewriteFailure, match="request_budget"):
        ledger.plan(data())
    assert not ledger.judgment_reservations and ledger.last_round == -1
    ledger, _ = edit_budget(enabled=False)
    with pytest.raises(ValueError, match="Inactive provider"):
        with ledger.call("judgment"):
            pytest.fail("Disabled judgment started")
    assert not ledger.metrics.snapshot()["model_called"]


@pytest.mark.parametrize("degree,seconds", [("polish", 120), ("rewrite", 240)])
@pytest.mark.parametrize("enabled", [False, True])
def test_v4_disabled_deadline_ignores_inactive_judgment_setting(degree, seconds, enabled):
    ledger, now = edit_budget(degree, enabled, **{degree + "_deadline_ms": 1})
    now[0] += 0.002
    if enabled:
        with pytest.raises(RewriteFailure, match="provider_timeout"):
            ledger.checkpoint()
    else:
        ledger.checkpoint()
        now[0] = 10 + seconds
        with pytest.raises(RewriteFailure, match="provider_timeout"):
            ledger.checkpoint()

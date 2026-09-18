import pytest

from copyeditor.metrics import Metrics
from copyeditor.providers.base import Usage
from copyeditor.rewrite_budget import RewriteBudget, RewriteFailure


def budget(now=None, pricing=None):
    now = [10.0] if now is None else now
    meter = Metrics(10.0, "model", pricing or {}, clock=lambda: now[0], degree="rewrite")
    return RewriteBudget(meter, clock=lambda: now[0])


@pytest.mark.parametrize("finish,invalid,code", [
    ("truncated", None, "generation_truncated"),
    ("stop", "invalid_response", "invalid_response"),
    ("stop", None, "request_budget"),
    ("blocked", None, "provider_error"),
    ("other", None, "invalid_response"),
    ("stop", "invalid_response", "invalid_response"),
    ("stop", "output_limit", "output_limit"),
    ("stop", None, "request_budget"),
])
def test_ctr01_error_precedence_table_retains_usage_and_prohibits_further_calls(finish, invalid, code):
    ledger = budget()
    call = ledger.start_call(0, is_regeneration=False)
    ledger.record_usage(call, Usage(1025, 8193, 10000))
    assert ledger.terminal_code is None
    with pytest.raises(RewriteFailure) as error:
        ledger.checkpoint(finish=finish, validation_error=invalid)
    assert error.value.code == code
    assert ledger.meter.snapshot()["usage"] == Usage(1025, 8193, 10000)._asdict()
    with pytest.raises(RewriteFailure) as stopped:
        ledger.start_call(0, is_regeneration=True)
    assert stopped.value.code == code
    assert ledger.meter.snapshot()["model_calls"] == 1
    assert not ledger.meter.snapshot()["regeneration_attempted"]


def test_ctr01_deadline_wins_over_all_unselected_errors_but_cleanup_cannot_replace_code():
    now = [249.999]
    ledger = budget(now)
    call = ledger.start_call(0, is_regeneration=False)
    ledger.record_usage(call, Usage(1025, 8193, None))
    now[0] = 250
    assert ledger.remaining == 0
    with pytest.raises(RewriteFailure, match="provider_timeout"):
        ledger.checkpoint(finish="truncated", validation_error="invalid_response")
    earlier = budget()
    with pytest.raises(RewriteFailure, match="generation_truncated"):
        earlier.checkpoint(finish="truncated")
    earlier.clock = lambda: 999
    with pytest.raises(RewriteFailure, match="generation_truncated"):
        earlier.checkpoint(provider_error="provider_error")


@pytest.mark.parametrize("estimate", [None, True, -1, 1.5, "0"])
def test_ctr01_bad_estimates_start_no_generation(estimate):
    ledger = budget()
    with pytest.raises(RewriteFailure, match="provider_error"):
        ledger.start_call(estimate, is_regeneration=False)
    assert ledger.meter.snapshot()["model_calls"] == 0 and ledger.reservations == []


@pytest.mark.parametrize("estimate,reserved", [(0, 1024), (1, 1026), (4, 1029), (208896, 262144)])
def test_ctr01_input_reservation_rounds_up_and_accepts_exact_ceiling(estimate, reserved):
    ledger = budget()
    ledger.start_call(estimate, is_regeneration=False)
    assert ledger.reservations == [(reserved, 8192)]
    assert ledger.meter.snapshot()["usage"] == dict.fromkeys(Usage._fields)
    assert ledger.meter.snapshot()["cost"] is None
    if reserved == 262144:
        with pytest.raises(RewriteFailure, match="request_budget"):
            ledger.start_call(0, is_regeneration=False)
        assert len(ledger.reservations) == 1


def test_ctr01_output_reservations_are_never_refunded_or_reused():
    ledger = budget()
    for i in range(17):
        call = ledger.start_call(0, is_regeneration=i > 8)
        ledger.record_usage(call, Usage(0, 0, 0))
        ledger.checkpoint(finish="stop")
    assert sum(item[1] for item in ledger.reservations) == 139264
    with pytest.raises(RewriteFailure, match="request_budget"):
        ledger.start_call(0, is_regeneration=False)
    assert ledger.meter.snapshot()["model_calls"] == 17
    assert ledger.meter.snapshot()["usage"] == Usage(0, 0, 0)._asdict()


@pytest.mark.parametrize("usage,exceeds", [(Usage(None, None, 999999), False),
    (Usage(1024, 8192, 999999), False), (Usage(1025, None, None), True),
    (Usage(None, 8193, None), True)])
def test_inv11_actual_component_overrun_is_separate_from_unknown_usage(usage, exceeds):
    ledger = budget()
    ledger.record_usage(ledger.start_call(0, is_regeneration=False), usage)
    assert ledger.overrun is exceeds
    assert ledger.meter.snapshot()["usage"] == usage._asdict()
    assert ledger.terminal_code is None


@pytest.mark.parametrize("failure", ["provider_error", "provider_timeout"])
def test_ctr01_estimation_failure_precedes_reservation_refusal(failure):
    ledger = budget()
    ledger.start_call(208896, is_regeneration=False)
    with pytest.raises(RewriteFailure, match=failure):
        ledger.checkpoint(provider_error=failure)
    with pytest.raises(RewriteFailure, match=failure):
        ledger.start_call(0, is_regeneration=False)
    assert ledger.meter.snapshot()["model_calls"] == 1


def test_inv11_overrun_preserves_actual_cost_even_when_finish_wins():
    ledger = budget(pricing={"model": {"input_per_million": 1, "output_per_million": 2, "currency": "USD"}})
    call = ledger.start_call(0, is_regeneration=False)
    ledger.record_usage(call, Usage(2000, 10000, 12000))
    with pytest.raises(RewriteFailure, match="generation_truncated"):
        ledger.checkpoint(finish="truncated")
    assert ledger.meter.snapshot()["cost"] == {"amount": "0.022000", "currency": "USD"}


def test_ctr01_preflight_deadline_and_refusal_have_no_started_slot():
    for now, estimate, code in [([250], 0, "provider_timeout"), ([10], 208897, "request_budget")]:
        ledger = budget(now)
        with pytest.raises(RewriteFailure, match=code):
            ledger.start_call(estimate, is_regeneration=False)
        assert ledger.meter.snapshot()["model_calls"] == 0
        assert ledger.meter.snapshot()["usage"] == Usage(0, 0, 0)._asdict()
        assert ledger.reservations == []

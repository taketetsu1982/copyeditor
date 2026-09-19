import asyncio
from contextlib import contextmanager
from decimal import Decimal

from .judgment_batch import JudgmentBudgetError, prepare_judgments
from .rewrite_budget import RewriteBudget


class JudgedBudget(RewriteBudget):
    def __init__(self, metrics, config):
        super().__init__(metrics.meters["editing"], metrics.clock)
        self.metrics, self.config = metrics, config
        self.deadline = metrics.started_at + config[f"judgment.{self.meter.degree}_deadline_ms"] / 1000
        self.judgment_reservations = []
        self.cancelled = False

    def checkpoint(self, *, provider_error=None, finish=None, validation_error=None,
                   output_limit=False, html_structure=False):
        if self.cancelled and self.terminal_code is None:
            raise asyncio.CancelledError()
        errors = {validation_error, "output_limit" if output_limit else None, "html_structure" if html_structure else None}
        selected = next((code for code in ("invalid_response", "output_limit", "html_structure", "request_budget") if code in errors), validation_error)
        super().checkpoint(provider_error=provider_error, finish=finish, validation_error=selected)

    def timeout(self, role):
        self.checkpoint()
        return min(self.remaining, 60 if role == "editing" else self.config["judgment.timeout_ms"] / 1000)

    def _money(self, editing, judgment):
        prices = [self.metrics.meters[role].price for role in ("editing", "judgment")]
        if any(price is None for price in prices) or prices[0]["currency"] != prices[1]["currency"]:
            return
        def amount(reservations, price):
            return sum(Decimal(value) * Decimal(str(price[key])) for reserved in reservations
                       for value, key in zip(reserved, ("input_per_million", "output_per_million"))) / Decimal(1000000)
        ceiling = amount([(262144, 16384 if self.meter.degree == "polish" else 139264)], prices[0])
        if amount(editing, prices[0]) + amount(judgment, prices[1]) > ceiling:
            self.checkpoint(validation_error="request_budget")

    def plan(self, data):
        self.checkpoint()
        failed = False
        try:
            prepared = prepare_judgments(data, policy_id=self.config["judgment.policy_version"],
                remaining_calls=self.config["judgment.max_calls"] - len(self.judgment_reservations),
                remaining_input_units=self.config["judgment.input_budget"] - sum(r[0] for r in self.judgment_reservations))
        except JudgmentBudgetError:
            failed = True
        if failed:
            self.checkpoint(validation_error="request_budget")
        added = [(batch.input_units, 65536) for batch in prepared.plan.batches]
        self._money(self.reservations, self.judgment_reservations + added)
        self.judgment_reservations.extend(added)
        return prepared

    @contextmanager
    def call(self, role, *, estimated_input=None, estimation=False, is_regeneration=False):
        self.checkpoint()
        if role not in ("editing", "judgment") or (estimation and role != "editing"):
            raise ValueError("Unknown operation")
        if estimation:
            self.metrics.estimations[role] += 1
            slot = None
        elif role == "editing":
            if type(estimated_input) is not int or estimated_input < 0:
                self.checkpoint(provider_error="provider_error")
            if len(self.meter.calls) >= self.meter.max_calls:
                self.checkpoint(validation_error="request_budget")
            reserved = ((5 * estimated_input + 3) // 4 + 1024, 8192)
            self._money(self.reservations + [reserved], self.judgment_reservations)
            slot = super().start_call(estimated_input, is_regeneration=is_regeneration)
        else:
            meter = self.metrics.meters[role]
            if len(meter.calls) >= len(self.judgment_reservations):
                self.checkpoint(validation_error="request_budget")
            slot = meter.start_call(is_regeneration=False)
        started = self.clock()
        try:
            yield slot
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.metrics.elapsed[role] += self.clock() - started

    def record_usage(self, role, slot, usage):
        self.metrics.record_usage(role, slot, usage)
        actual = self.metrics.meters[role].calls[slot]
        reserved = (self.reservations if role == "editing" else self.judgment_reservations)[slot]
        self.overrun |= any(value is not None and value > limit for value, limit in zip(actual[:2], reserved))

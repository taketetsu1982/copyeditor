from decimal import Decimal, ROUND_HALF_UP
from time import monotonic

from copyeditor.providers.base import Usage


class Metrics:
    def __init__(self, started_at, model, pricing, clock=monotonic, *, degree="polish"):
        if degree not in ("polish", "rewrite"):
            raise ValueError("Unknown editing degree")
        self.degree = degree
        self.max_calls = 2 if degree == "polish" else 17
        self.regenerated = False
        self.started_at = started_at
        self.clock = clock
        self.price = dict(pricing[model]) if model in pricing else None
        self.calls = []

    def start_call(self, *, is_regeneration=None):
        if len(self.calls) >= self.max_calls:
            raise ValueError("Model call limit exceeded")
        if is_regeneration is None:
            if self.degree == "rewrite":
                raise ValueError("Rewrite calls require an explicit regeneration marker")
            is_regeneration = bool(self.calls)
        if type(is_regeneration) is not bool:
            raise ValueError("Regeneration marker must be boolean")
        self.regenerated |= is_regeneration
        self.calls.append(None)
        return len(self.calls) - 1

    def record_usage(self, call, usage: Usage):
        if not 0 <= call < len(self.calls) or self.calls[call] is not None:
            raise ValueError("Usage requires an unrecorded started call")
        self.calls[call] = usage

    def snapshot(self):
        totals = []
        for component in range(3):
            values = [call[component] if call is not None else None for call in self.calls]
            totals.append(None if None in values else sum(values))
        usage = Usage(*totals)
        cost = None
        if self.calls and self.price and None not in totals[:2]:
            amount = (
                Decimal(usage.input_tokens) * Decimal(str(self.price["input_per_million"]))
                + Decimal(usage.output_tokens) * Decimal(str(self.price["output_per_million"]))
            ) / Decimal(1000000)
            cost = {
                "amount": format(amount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f"),
                "currency": self.price["currency"],
            }
        return {
            "usage": usage._asdict(),
            "cost": cost,
            "latency_ms": round((self.clock() - self.started_at) * 1000),
            "model_calls": len(self.calls),
            "regeneration_attempted": self.regenerated,
        }

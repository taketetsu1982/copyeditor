from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
from time import monotonic

from .metrics import Metrics
from .providers.base import Usage


class JudgedMetrics:
    def __init__(self, started_at, editing_model, pricing, clock=monotonic, *, degree="polish"):
        self.started_at, self.clock = started_at, clock
        self.models = {"editing": editing_model, "judgment": "jev-1.13.0"}
        self.meters = {role: Metrics(started_at, model, pricing, clock, degree=degree)
                       for role, model in self.models.items()}
        self.meters["judgment"].max_calls = 64
        self.estimations = {role: 0 for role in self.models}
        self.elapsed = {role: 0.0 for role in self.models}

    @contextmanager
    def call(self, role, *, estimation=False, is_regeneration=False):
        meter = self.meters[role]
        if estimation and role != "editing":
            raise ValueError("Judgment has no estimation calls")
        if role == "judgment" and is_regeneration:
            raise ValueError("Judgment calls are not regeneration")
        if estimation:
            self.estimations[role] += 1
            slot = None
        else:
            slot = meter.start_call(is_regeneration=is_regeneration)
        started = self.clock()
        try:
            yield slot
        finally:
            self.elapsed[role] += self.clock() - started

    def record_usage(self, role, slot, usage: Usage):
        values = (getattr(usage, name, None) for name in Usage._fields)
        normalized = Usage(*(v if type(v) is int and v >= 0 else None for v in values))
        self.meters[role].record_usage(slot, normalized)

    def snapshot(self):
        rows = []
        for role, provider in (("editing", "vertex"), ("judgment", "typesafe")):
            if role not in self.meters:
                continue
            measured = self.meters[role].snapshot()
            rows.append({"role": role, "provider": provider, "model": self.models[role],
                         "model_calls": measured["model_calls"],
                         "estimation_calls": self.estimations[role],
                         "usage": measured["usage"], "cost": measured["cost"],
                         "latency_ms": round(self.elapsed[role] * 1000)})
        called = [row for row in rows if row["model_calls"]]
        cost = None
        if (called and all(row["cost"] is not None for row in called)
                and len({row["cost"]["currency"] for row in called}) == 1):
            # Rounded provider amounts lose fractions needed by the request total.
            amount = sum((
                Decimal(row["usage"]["input_tokens"])
                * Decimal(str(self.meters[row["role"]].price["input_per_million"]))
                + Decimal(row["usage"]["output_tokens"])
                * Decimal(str(self.meters[row["role"]].price["output_per_million"]))
            ) / Decimal(1000000) for row in called)
            cost = {"amount": format(amount.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f"),
                    "currency": called[0]["cost"]["currency"]}
        return {"providers": rows, "cost": cost,
                "latency_ms": round((self.clock() - self.started_at) * 1000),
                "model_called": bool(called),
                "regeneration_attempted": self.meters["editing"].regenerated}


class EditMetrics(JudgedMetrics):
    def __init__(self, *args, judgment_enabled=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.meters["editing"].max_calls = 2 if self.meters["editing"].degree == "polish" else 16
        if not judgment_enabled:
            del self.meters["judgment"]

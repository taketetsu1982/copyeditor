from .config import pricing
from .judgment_v2 import POLICY_ID


class JudgmentSecret:
    __slots__ = ("_value",)

    def __init__(self, value):
        self._value = value

    def reveal(self):
        return self._value


LIMITS = {"timeout_ms": (10000, 60000), "polish_deadline_ms": (120000, 120000),
          "rewrite_deadline_ms": (240000, 240000), "max_calls": (64, 64), "input_budget": (262144, 262144)}
JUDGMENT_FIELDS = {
    "enabled": (False, lambda v: type(v) is bool),
    "model": ("jev-1.13.0", lambda v: v == "jev-1.13.0"),
    "policy_version": (POLICY_ID, lambda v: type(v) is str and bool(v)),
    "thresholds_version": (None, lambda v: v is None or type(v) is str and bool(v)),
    **{key: (default, lambda v, maximum=maximum: type(v) is int and 1 <= v <= maximum)
       for key, (default, maximum) in LIMITS.items()},
    "pricing": ({}, pricing),
}

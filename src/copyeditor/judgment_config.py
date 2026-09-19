import json
import os
from decimal import Decimal

from .config import ConfigError, ResolvedConfig, SECRETS, freeze, matches, pricing
from .judgment import COMPATIBLE_PAIRS, POLICY_ID, POLICIES, THRESHOLD_ID, THRESHOLDS


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
    "policy_version": (POLICY_ID, lambda v: type(v) is str and v in POLICIES),
    "thresholds_version": (THRESHOLD_ID, lambda v: type(v) is str and v in THRESHOLDS),
    **{key: (default, lambda v, maximum=maximum: type(v) is int and 1 <= v <= maximum)
       for key, (default, maximum) in LIMITS.items()},
    "pricing": ({}, pricing),
}


def resolve_judgment_config(explicit, environ=None):
    env = os.environ if environ is None else environ
    if type(explicit) is not dict or any(key not in JUDGMENT_FIELDS for key in explicit):
        raise ConfigError(field="judgment")
    resolved = {}
    for key, (default, validate) in JUDGMENT_FIELDS.items():
        name, variable = "judgment." + key, "COPYEDITOR_JUDGMENT_" + key.upper()
        failed = False
        try:
            value, encoded = ((explicit[key], False) if key in explicit
                              else (env.get(variable, default), variable in env))
            if isinstance(value, str) and not encoded and value.startswith("${") and value.endswith("}"):
                variable = value[2:-1]
                if not matches(r"[A-Z][A-Z0-9_]*", variable) or variable in (*SECRETS, "TYPESAFE_API_KEY"):
                    raise ValueError()
                value, encoded = env.get(variable), True
                if not value:
                    raise ValueError()
            if encoded and type(default) in (bool, int, dict):
                value = json.loads(value, parse_float=Decimal, parse_constant=lambda _: None)
            if not validate(value):
                raise ValueError()
            resolved[name] = value
        except Exception:
            failed = True
        if failed:
            raise ConfigError(field=name)
    if (resolved["judgment.policy_version"], resolved["judgment.thresholds_version"]) not in COMPATIBLE_PAIRS:
        raise ConfigError(field="judgment.thresholds_version")
    secrets = {}
    if resolved["judgment.enabled"]:
        key, failed = None, False
        try:
            key = env.get("TYPESAFE_API_KEY")
        except Exception:
            failed = True
        if failed:
            raise ConfigError(field="judgment.credentials")
        if key is None or key == "":
            raise ConfigError("missing_required", "judgment.credentials")
        if not matches(r"[\x21-\x7e]{1,4096}", key):
            raise ConfigError(field="judgment.credentials")
        secrets["TYPESAFE_API_KEY"] = JudgmentSecret(key)
    return ResolvedConfig(freeze(resolved), freeze(secrets))

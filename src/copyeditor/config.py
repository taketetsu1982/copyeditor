import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit
from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor
from ruamel.yaml.tokens import AliasToken, DirectiveToken

class ConfigError(ValueError):
    def __init__(self, code="invalid_config", field="config"):
        self.code, self.field = code, field
        super().__init__(f"ERROR: {code} at {field}.")
class DecimalConstructor(SafeConstructor):
    pass
DecimalConstructor.add_constructor("tag:yaml.org,2002:float", lambda loader, node: Decimal(
    node.value.lower().replace("_", "").replace(".inf", "Infinity").replace(".nan", "NaN")))

def strict_yaml(text):
    try:
        yaml = YAML(typ="safe", pure=True)
        yaml.version, yaml.Constructor = (1, 2), DecimalConstructor
        for token in yaml.scan(text):
            if isinstance(token, AliasToken) or (isinstance(token, DirectiveToken)
                                                and token.name == "YAML" and token.value != (1, 2)):
                raise ValueError()
        def check(node):
            if node.tag not in {f"tag:yaml.org,2002:{t}" for t in ("map", "seq", "str", "int", "float", "bool", "null")}:
                raise ValueError()
            if node.id == "mapping":
                for key, value in node.value:
                    check(key)
                    check(value)
            elif node.id == "sequence":
                for value in node.value:
                    check(value)
        node = yaml.compose(text)
        if node is not None:
            check(node)
        return yaml.load(text)
    except Exception:
        raise ConfigError() from None
def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value, re.ASCII) is not None
def nonblank(value, limit):
    return isinstance(value, str) and 1 <= len(value) <= limit and bool(value.strip())
def number(value, low, high):
    return type(value) in (int, float, Decimal) and Decimal(str(value)).is_finite() and low <= value <= high
def domain(value):
    return isinstance(value, str) and len(value) <= 253 and "." in value and all(
        matches(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in value.split("."))
def email(value):
    return matches(r"[^\s@]{1,64}@[^@]+", value) and value.isascii() and len(value) <= 254 and domain(value.split("@")[1])
def array(value, limit, valid):
    return isinstance(value, list) and len(value) <= limit and all(valid(v) for v in value) and len(set(value)) == len(value)
def host(value):
    if value == "localhost":
        return True
    try:
        ipaddress.ip_address(value if isinstance(value, str) and "%" not in value else "")
        return True
    except ValueError:
        return False
def origin(value):
    if not isinstance(value, str) or not value.isascii() or re.search(r"[\s\x00-\x1f\x7f?#]", value):
        return False
    url = urlsplit(value)
    return (url.scheme == "https" and bool(url.hostname) and url.username is None and url.password is None
            and url.path in ("", "/") and not url.netloc.endswith(":") and (url.port is None or 1 <= url.port <= 65535)
            and (host(url.hostname) or domain(url.hostname.lower()) or matches(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", url.hostname)))

MODEL = r"[A-Za-z0-9._-]{1,128}"
SECRETS = ("GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_SIGNING_KEY")
def pricing(value):
    return isinstance(value, dict) and len(value) <= 100 and all(
        matches(MODEL, model) and isinstance(entry, dict) and set(entry) == {"currency", "input_per_million", "output_per_million"}
        and matches(r"[A-Z]{3}", entry["currency"]) and all(number(entry[k], 0, 1000000)
        and Decimal(str(entry[k])).quantize(Decimal("0.000001")) == Decimal(str(entry[k])) for k in ("input_per_million", "output_per_million"))
        for model, entry in value.items())

SCHEMA = {
    "provider": ("COPYEDITOR_PROVIDER", "vertex", lambda v: v == "vertex"),
    "model": ("COPYEDITOR_MODEL", "gemini-3.1-flash-lite", lambda v: matches(MODEL, v)),
    "thinking": ("COPYEDITOR_THINKING", "low", lambda v: v in ("minimal", "low", "medium", "high")),
    "protected_terms": ("COPYEDITOR_PROTECTED_TERMS", [], lambda v: array(v, 1024, lambda s: nonblank(s, 128))),
    "length_ratio.min": ("COPYEDITOR_LENGTH_RATIO_MIN", 0.5, lambda v: number(v, 0, 1) and v > 0),
    "length_ratio.max": ("COPYEDITOR_LENGTH_RATIO_MAX", 2.0, lambda v: number(v, 1, 4)),
    "vertex.project": ("GOOGLE_CLOUD_PROJECT", None, lambda v: matches(r"[A-Za-z0-9_-]{1,128}", v)),
    "vertex.location": ("GOOGLE_CLOUD_LOCATION", "global", lambda v: matches(r"[a-z0-9-]{1,64}", v)),
    "auth.mode": ("COPYEDITOR_AUTH_MODE", "none", lambda v: v in ("none", "google")),
    "auth.allowed_domains": ("COPYEDITOR_ALLOWED_DOMAINS", [], lambda v: array(v, 100, domain)),
    "auth.allowed_emails": ("COPYEDITOR_ALLOWED_EMAILS", [], lambda v: array(v, 1000, email)),
    "auth.client_id": ("GOOGLE_OAUTH_CLIENT_ID", None, lambda v: v is None or nonblank(v, 256)),
    "auth.base_url": ("BASE_URL", None, lambda v: v is None or origin(v)),
    "pricing": ("COPYEDITOR_PRICING", {}, pricing),
    "server.host": ("COPYEDITOR_HOST", "0.0.0.0", host),
    "server.port": ("PORT", 8080, lambda v: type(v) is int and 1 <= v <= 65535),
}
def _judgment_valid(key, value):
    # The internal resolver imports config primitives; defer its import to avoid a cycle.
    from .judgment_config import JUDGMENT_FIELDS
    return JUDGMENT_FIELDS[key][1](value)

SCHEMA.update({"judgment." + key: ("COPYEDITOR_JUDGMENT_" + key.upper(), default,
    lambda value, key=key: _judgment_valid(key, value)) for key, default in dict(
    enabled=False, model="jev-1.13.0", policy_version="reference-gate-v2", thresholds_version=None,
    timeout_ms=10000, polish_deadline_ms=120000, rewrite_deadline_ms=240000, max_calls=64,
    input_budget=262144, pricing={}).items()})

def freeze(value):
    return MappingProxyType({k: freeze(v) for k, v in value.items()}) if isinstance(value, dict) else tuple(value) if isinstance(value, list) else value

@dataclass(frozen=True)
class ResolvedConfig:
    values: object = field(repr=False)
    secrets: object = field(repr=False)
    def __getitem__(self, key):
        return self.values[key]
    rules: object = field(default=None, repr=False)

def load_config(path=Path("/etc/copyeditor/config.yaml"), environ=None, **options):
    from .config_v4 import load_config as resolve
    return resolve(path, environ, **options)

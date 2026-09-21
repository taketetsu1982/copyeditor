"""Unconnected generation-4 configuration; no default language or guessed thresholds."""
import json
import os
from decimal import Decimal
from pathlib import Path

from .config import ConfigError, ResolvedConfig, SCHEMA as LEGACY, SECRETS, freeze, matches, strict_yaml
from .judgment_config import JudgmentSecret
from .judgment_v2 import COMPATIBLE_PAIRS, POLICY_ID, POLICIES, THRESHOLDS


def load_config(path=Path("/etc/copyeditor/config.yaml"), environ=None, *, thresholds=THRESHOLDS, pairs=COMPATIBLE_PAIRS):
    env = os.environ if environ is None else environ
    if "COPYEDITOR_DEFAULT_LANGUAGE" in env:
        raise ConfigError()
    schema = {key: value for key, value in LEGACY.items() if key != "default_language"}
    schema["judgment.policy_version"] = ("COPYEDITOR_JUDGMENT_POLICY_VERSION", POLICY_ID,
                                       lambda v: type(v) is str and v in POLICIES)
    schema["judgment.thresholds_version"] = ("COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION", None,
                                           lambda v: v is None or type(v) is str and v in thresholds)
    try:
        raw = strict_yaml(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, UnicodeError):
        raise ConfigError() from None
    explicit, resolved = {}, {}
    def flatten(value, parent=""):
        if type(value) is not dict:
            raise ConfigError(field=parent or "config")
        for key, item in value.items():
            if type(key) is not str or "." in key:
                raise ConfigError(field=parent or "config")
            name = f"{parent}.{key}" if parent else key
            if name in schema:
                explicit[name] = item
            elif any(k.startswith(name + ".") for k in schema):
                flatten(item, name)
            else:
                raise ConfigError(field=parent or "config")
    flatten(raw)
    for name, (variable, default, validate) in schema.items():
        failed = False
        try:
            value, encoded = ((explicit[name], False) if name in explicit else (env.get(variable, default), variable in env))
            if type(value) is str and not encoded and value.startswith("${") and value.endswith("}"):
                variable = value[2:-1]
                if not matches(r"[A-Z][A-Z0-9_]*", variable) or variable in (*SECRETS, "TYPESAFE_API_KEY"):
                    raise ValueError()
                value, encoded = env.get(variable), True
                if not value:
                    raise ValueError()
            if encoded and (type(default) in (bool, int, float, list, dict) or default is None and value == "null"):
                value = json.loads(value, parse_float=Decimal, parse_constant=lambda _: None)
            if name == "vertex.project" and name not in explicit and variable not in env:
                raise ConfigError("missing_required", name)
            if name == "provider" and type(value) is str and value != "vertex":
                raise ConfigError("unsupported_provider", name)
            if not validate(value):
                raise ValueError()
            resolved[name] = value.rstrip("/") if name == "auth.base_url" and value else value
        except ConfigError:
            raise
        except Exception:
            failed = True
        if failed:
            raise ConfigError(field=name)
    threshold = resolved["judgment.thresholds_version"]
    if threshold is None and resolved["judgment.enabled"]:
        raise ConfigError("missing_required", "judgment.thresholds_version")
    if threshold is not None and (resolved["judgment.policy_version"], threshold) not in pairs:
        raise ConfigError(field="judgment.thresholds_version")
    secrets = {}
    if resolved["auth.mode"] == "google":
        secrets = {key: env.get(key) for key in SECRETS}
        for field, present in (("auth.client_id", resolved["auth.client_id"]), ("auth.base_url", resolved["auth.base_url"]),
            ("auth.allowed_domains", resolved["auth.allowed_domains"] or resolved["auth.allowed_emails"]),
            ("auth", all(secrets.values()) and len(secrets[SECRETS[1]].encode()) >= 32)):
            if not present:
                raise ConfigError("missing_required", field)
    if resolved["judgment.enabled"]:
        failed = False
        try:
            key = env.get("TYPESAFE_API_KEY")
        except Exception:
            failed, key = True, None
        if failed:
            raise ConfigError(field="judgment.credentials")
        if key is None or key == "":
            raise ConfigError("missing_required", "judgment.credentials")
        if not matches(r"[\x21-\x7e]{1,4096}", key):
            raise ConfigError(field="judgment.credentials")
        secrets["TYPESAFE_API_KEY"] = JudgmentSecret(key)
    return ResolvedConfig(freeze(resolved), freeze(secrets))

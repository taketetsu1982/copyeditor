"""Environment-only configuration; never expose configuration values in errors."""
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class ConfigError(ValueError):
    def __init__(self):
        super().__init__("Invalid server configuration.")


def domain(value):
    return isinstance(value, str) and len(value) <= 253 and "." in value and all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part, re.ASCII)
        for part in value.split("."))


def email(value):
    return (isinstance(value, str) and value.isascii() and len(value) <= 254
            and re.fullmatch(r"[^\s@]{1,64}@[^@]+", value, re.ASCII) is not None
            and domain(value.split("@")[1]))


@dataclass(frozen=True)
class ResolvedConfig:
    values: dict = field(repr=False)
    secrets: dict = field(repr=False)

    def __getitem__(self, key):
        return self.values[key]


def load_config(environ=None):
    env = os.environ if environ is None else environ
    try:
        values = {}
        for key, variable, default, pattern in (
            ("vertex.project", "GOOGLE_CLOUD_PROJECT", "", r"[A-Za-z0-9_-]{1,128}"),
            ("vertex.location", "GOOGLE_CLOUD_LOCATION", "global", r"[a-z0-9-]{1,64}"),
            ("model", "COPYEDITOR_MODEL", "gemini-3.7-flash", r"[A-Za-z0-9._-]{1,128}"),
            ("auth.mode", "COPYEDITOR_AUTH_MODE", "none", r"google|none"),
        ):
            value = env.get(variable, default)
            if not isinstance(value, str) or not re.fullmatch(pattern, value, re.ASCII):
                raise ValueError()
            values[key] = value
        values["server.port"] = int(env.get("PORT", "8080"))
        if not 1 <= values["server.port"] <= 65535:
            raise ValueError()
        secrets = {}
        if values["auth.mode"] == "google":
            for kind, validate in (("domains", domain), ("emails", email)):
                entries = json.loads(env.get("COPYEDITOR_ALLOWED_" + kind.upper(), "[]"))
                if not isinstance(entries, list) or not all(validate(v) for v in entries):
                    raise ValueError()
                values["auth.allowed_" + kind] = tuple(entries)
            if not (values["auth.allowed_domains"] or values["auth.allowed_emails"]):
                raise ValueError()
            base = env.get("BASE_URL", "")
            url = urlsplit(base)
            if (not base.isascii() or re.search(r"[\s\x00-\x1f\x7f?#\\]", base)
                    or url.scheme != "https" or not url.hostname or url.username is not None
                    or url.password is not None or url.path not in ("", "/")
                    or url.netloc.endswith(":") or (url.port is not None and not 1 <= url.port <= 65535)):
                raise ValueError()
            values["auth.base_url"] = base.rstrip("/")
            values["auth.client_id"] = env.get("GOOGLE_OAUTH_CLIENT_ID", "")
            secrets = {key: env.get(key, "") for key in ("GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_SIGNING_KEY")}
            if (not values["auth.client_id"].strip() or not all(v.strip() for v in secrets.values())
                    or len(secrets["OAUTH_SIGNING_KEY"].encode()) < 32):
                raise ValueError()
        return ResolvedConfig(values, secrets)
    except Exception:
        raise ConfigError() from None

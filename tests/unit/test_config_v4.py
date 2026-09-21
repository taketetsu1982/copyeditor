"""V4 resolution and fail-closed registry, without provider initialization."""
from pathlib import Path

import pytest

from copyeditor.config import ConfigError, load_config as legacy_load
from copyeditor.config_v4 import load_config
from copyeditor.judgment_v2 import POLICY_ID


class PoisonSecrets(dict):
    def get(self, key, default=None):
        if key in ("TYPESAFE_API_KEY", "GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_SIGNING_KEY", "COPYEDITOR_DEFAULT_LANGUAGE"):
            raise AssertionError("Secret or removed environment value accessed")
        return super().get(key, default)


def resolve(tmp_path, text="{}", env=None, **kwargs):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return load_config(path, env if env is not None else {"GOOGLE_CLOUD_PROJECT": "synthetic-project"}, **kwargs)


def test_disabled_defaults_have_null_threshold_and_never_read_secrets(tmp_path):
    config = resolve(tmp_path, env=PoisonSecrets(GOOGLE_CLOUD_PROJECT="synthetic-project"))
    assert config["judgment.enabled"] is False and config["judgment.thresholds_version"] is None
    assert config["judgment.policy_version"] == POLICY_ID and "default_language" not in config.values
    assert not config.secrets
    assert legacy_load(tmp_path / "config.yaml", {"GOOGLE_CLOUD_PROJECT": "synthetic-project"})["default_language"] == "ja"


@pytest.mark.parametrize("text,code,field", [
    ("default_language: ja", "invalid_config", "config"),
    ("judgment: {enabled: true}", "missing_required", "judgment.thresholds_version"),
    ("judgment: {thresholds_version: gate-delta-v2}", "invalid_config", "judgment.thresholds_version"),
    ("judgment: {thresholds_version: gate-verify-v1}", "invalid_config", "judgment.thresholds_version"),
    ("judgment: {policy_version: reference-gate-action-v1}", "invalid_config", "judgment.policy_version"),
    ("judgment: {timeout_ms: true}", "invalid_config", "judgment.timeout_ms"),
    ("judgment: {thresholds_version: '${TYPESAFE_API_KEY}'}", "invalid_config", "judgment.thresholds_version"),
    ("judgment: null", "invalid_config", "judgment"), ("", "invalid_config", "config"),
])
def test_invalid_or_unregistered_config_fails_before_secrets(tmp_path, text, code, field):
    with pytest.raises(ConfigError) as error:
        resolve(tmp_path, text, PoisonSecrets(GOOGLE_CLOUD_PROJECT="synthetic-project"))
    assert (error.value.code, error.value.field) == (code, field)


def test_removed_environment_is_rejected_without_reading_its_value(tmp_path):
    with pytest.raises(ConfigError) as error:
        resolve(tmp_path, env=PoisonSecrets(GOOGLE_CLOUD_PROJECT="synthetic-project", COPYEDITOR_DEFAULT_LANGUAGE="private"))
    assert error.value.field == "config" and "private" not in str(error.value)


def test_explicit_null_and_placeholder_precedence(tmp_path):
    env = {"GOOGLE_CLOUD_PROJECT": "synthetic-project", "COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION": "retired",
           "COPYEDITOR_JUDGMENT_TIMEOUT_MS": "invalid", "SELECTED": "42", "COPYEDITOR_PRICING": "invalid"}
    config = resolve(tmp_path, "judgment: {thresholds_version: null, timeout_ms: '${SELECTED}'}\npricing: {}", env)
    assert config["judgment.thresholds_version"] is None and config["judgment.timeout_ms"] == 42
    assert not config["pricing"]
    config = resolve(tmp_path, env={"GOOGLE_CLOUD_PROJECT": "synthetic-project", "COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION": "null"})
    assert config["judgment.thresholds_version"] is None


def test_registered_pair_requires_secret_only_after_compatibility(tmp_path):
    env = {"GOOGLE_CLOUD_PROJECT": "synthetic-project", "TYPESAFE_API_KEY": "synthetic-secret"}
    text = "judgment: {enabled: true, thresholds_version: synthetic}"
    with pytest.raises(ConfigError) as error:
        resolve(tmp_path, text, PoisonSecrets(env), thresholds={"synthetic": {}}, pairs=set())
    assert error.value.field == "judgment.thresholds_version"
    config = resolve(tmp_path, text, env, thresholds={"synthetic": {}}, pairs={(POLICY_ID, "synthetic")})
    assert config.secrets["TYPESAFE_API_KEY"].reveal() == "synthetic-secret"
    assert "synthetic-secret" not in repr(config)

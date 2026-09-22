"""Configuration and credential observations (partial AC evidence).
AC-08-1, AC-08-7: disabled secret isolation and explicit startup failures.
AC-08-12, AC-08-14: bounded configuration and rejection of obsolete registry IDs."""
import json

import pytest

from copyeditor.config import ConfigError, SCHEMA, load_config, strict_yaml
from copyeditor.judgment_v2 import POLICY_ID
from tests.contracts.harness import FIXTURE_THRESHOLDS
from copyeditor.judgment_config import JUDGMENT_FIELDS, LIMITS


@pytest.fixture
def resolve(tmp_path):
    def invoke(explicit, env):
        # Credential cases use an explicit synthetic registry, never production registration.
        if isinstance(explicit, dict) and explicit.get("enabled") is True:
            explicit = {"thresholds_version": "synthetic", **explicit}
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"judgment": explicit}))
        env.setdefault("GOOGLE_CLOUD_PROJECT", "project")
        return load_config(path, env, thresholds=FIXTURE_THRESHOLDS, pairs={(POLICY_ID, "synthetic")})
    return invoke


class Poison(dict):
    def __init__(self, **values):
        super().__init__(values)
        self.secret_reads = 0

    def get(self, key, default=None):
        if key == "TYPESAFE_API_KEY":
            self.secret_reads += 1
            raise AssertionError("Secret lookup forbidden")
        return super().get(key, default)

    def __contains__(self, key):
        if key == "TYPESAFE_API_KEY":
            self.secret_reads += 1
            raise AssertionError("Secret membership check forbidden")
        return super().__contains__(key)


def test_disabled_defaults_do_not_read_or_keep_secret(resolve):
    for env in (Poison(), Poison(TYPESAFE_API_KEY=object())):
        config = resolve({}, env)
        assert config["judgment.enabled"] is False
        assert config["judgment.policy_version"] == POLICY_ID
        assert config["judgment.thresholds_version"] is None
        assert not config.secrets


@pytest.mark.parametrize("leaf", JUDGMENT_FIELDS)
def test_selected_config_overrides_poisoned_env_and_placeholder_overrides_normal_env(leaf, resolve):
    value = JUDGMENT_FIELDS[leaf][0]
    variable = "COPYEDITOR_JUDGMENT_" + leaf.upper()
    env = {variable: "invalid", "SELECTED": json.dumps(value) if value is None or type(value) in (bool, int, dict) else value}
    expected = {} if leaf == "pricing" else value
    assert resolve({leaf: value}, env)["judgment." + leaf] == expected
    assert resolve({leaf: "${SELECTED}"}, env)["judgment." + leaf] == expected
    assert resolve({}, {variable: env["SELECTED"]})["judgment." + leaf] == expected
    with pytest.raises(ConfigError):
        resolve({}, {variable: "invalid"})


@pytest.mark.parametrize("leaf", LIMITS)
def test_integer_bounds_and_types_are_strict_even_when_disabled(leaf, resolve):
    maximum = LIMITS[leaf][1]
    for value in (1, maximum):
        assert resolve({leaf: value}, {})["judgment." + leaf] == value
    for value in (0, maximum + 1, True, 1.0, "1", None, float("nan"), float("inf")):
        with pytest.raises(ConfigError) as caught:
            resolve({leaf: value}, {})
        assert caught.value.field == "judgment." + leaf


@pytest.mark.parametrize("leaf,value", [("enabled", 1), ("enabled", "true"), ("enabled", None),
    ("model", "jev-latest"), ("model", "jev-preview"), ("policy_version", "expression-v1"),
    ("policy_version", "state-action-v1"), ("thresholds_version", "conservative-v1"),
    ("thresholds_version", "state-action-conservative-v1"), ("pricing", None)])
def test_obsolete_ids_and_invalid_inactive_fields_fail(leaf, value, resolve):
    with pytest.raises(ConfigError) as caught:
        resolve({leaf: value}, {})
    assert caught.value.code == "invalid_config" and caught.value.field == "judgment." + leaf


@pytest.mark.parametrize("value", ["${TYPESAFE_API_KEY}", "${OAUTH_SIGNING_KEY}", "${GOOGLE_OAUTH_CLIENT_SECRET}",
                                    "${ABSENT}", "${bad}", "${EMPTY}", "${RECURSIVE}"])
def test_secret_and_unresolved_placeholders_fail_without_secret_lookup(value, resolve):
    env = Poison(EMPTY="", RECURSIVE="${OTHER}")
    with pytest.raises(ConfigError):
        resolve({"model": value}, env)
    assert env.secret_reads == 0


@pytest.mark.parametrize("value,code", [(None, "missing_required"), ("", "missing_required"),
    ("contains space", "invalid_config"), ("tab\t", "invalid_config"), ("newline\n", "invalid_config"),
    ("\x00", "invalid_config"), ("\x7f", "invalid_config"), ("非ASCII", "invalid_config"),
    ("x" * 4097, "invalid_config"), (123, "invalid_config")])
def test_enabled_secret_errors_are_fixed_and_do_not_chain_values(value, code, resolve):
    with pytest.raises(ConfigError) as caught:
        resolve({"enabled": True}, {"TYPESAFE_API_KEY": value})
    assert str(caught.value) == f"ERROR: {code} at judgment.credentials."
    assert caught.value.__context__ is None


def test_enabled_handle_is_not_represented_or_json_serialized(resolve):
    for secret in ("synthetic-test-sentinel", "x" * 4096):
        config = resolve({"enabled": True}, {"TYPESAFE_API_KEY": secret})
        handle = config.secrets["TYPESAFE_API_KEY"]
        assert handle.reveal() == secret
        assert secret not in repr(config) + repr(config.secrets) + repr(handle) + repr(config.values)
        with pytest.raises(TypeError):
            json.dumps(handle)


def test_closed_mapping_prices_and_public_loader_are_connected(tmp_path, resolve):
    for value in (None, [], {"key": "sentinel"}, {"endpoint": "https://invalid"}, {"enabled.extra": True}):
        with pytest.raises(ConfigError) as caught:
            resolve(value, {})
        assert caught.value.field == "judgment"
    prices = {"jev-1.13.0": dict(currency="USD", input_per_million=0.5, output_per_million=0)}
    env = {"COPYEDITOR_JUDGMENT_PRICING": json.dumps(prices)}
    assert not resolve({"pricing": {}}, env)["judgment.pricing"]
    assert resolve({}, env)["judgment.pricing"] == prices
    prices["jev-1.13.0"]["output_per_million"] = -1
    with pytest.raises(ConfigError):
        resolve({"pricing": prices}, {})
    with pytest.raises(ConfigError):
        strict_yaml("enabled: true\nenabled: false\n")
    assert {key.removeprefix("judgment.") for key in SCHEMA if key.startswith("judgment.")} == set(JUDGMENT_FIELDS)
    path = tmp_path / "config.yaml"
    path.write_text("judgment: {enabled: true}\n")
    with pytest.raises(ConfigError):
        load_config(path, {"GOOGLE_CLOUD_PROJECT": "project"})

    path.write_text("judgment: {enabled: false}\n")
    assert load_config(path, {"GOOGLE_CLOUD_PROJECT": "project"})["judgment.enabled"] is False

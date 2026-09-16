import json
from pathlib import Path
import pytest
from copyeditor.config import ConfigError, SCHEMA, load_config, strict_yaml

pytestmark = pytest.mark.consumer("CTR-04")
CASES = [
    ("provider", "vertex", [None, True, "other"]),
    ("model", "model_1-2.3", ["", "é", "a" * 129]),
    ("thinking", "high", ["LOW", 1, None]),
    ("default_language", "en-us", ["JA", "e", "a" * 36]),
    ("protected_terms", [" term "], [[" "], ["x", "x"], ["a" * 129], [str(i) for i in range(1025)]]),
    ("length_ratio.min", 1, [0, 1.01, True, float("inf")]),
    ("length_ratio.max", 4, [0.99, 4.01, False, float("nan")]),
    ("vertex.project", "project_1", [None, "", "x" * 129, "a.b"]),
    ("vertex.location", "us-central1", ["", "GLOBAL", "x" * 65]),
    ("auth.mode", "none", ["other", None, 1]),
    ("auth.allowed_domains", ["example.com"], [["EXAMPLE.com"], ["-a.com"], ["a.com."], ["a"], ["a.com"] * 101]),
    ("auth.allowed_emails", ["A+b@example.com"], [["a@@b.com"], ["a@EXAMPLE.com"], ["a b@c.com"], ["a" * 65 + "@b.com"]]),
    ("auth.client_id", "client", ["", " ", "a" * 257, 1]),
    ("auth.base_url", "https://example.com", ["http://a.com", "https://u@a.com", "https://a.com/?", "https://a.com/#", "https://a.com/x", "https://a.com:bad"]),
    ("pricing", {"m": {"currency": "USD", "input_per_million": 0, "output_per_million": 1000000}}, [{"m": {}}, *[{"m": {"currency": "USD", "input_per_million": p, "output_per_million": 1}} for p in (-1, 1000001, 0.0000001, True)], {"m": {"currency": "usd", "input_per_million": 0, "output_per_million": 1}}]),
    ("server.host", "::1", ["example.com", 1, "999.1.1.1"]),
    ("server.port", 65535, [0, 65536, True, 1.5]),
]

def write_config(tmp_path, name, value):
    parts = name.split(".")
    data = {parts[-1]: value}
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps({parts[0]: data} if len(parts) == 2 else data), encoding="utf-8")
    return path
@pytest.mark.parametrize("name,good,bad", CASES, ids=[case[0] for case in CASES])
def test_ac_05_2_leaf_validation_and_precedence(tmp_path, name, good, bad):
    variable = SCHEMA[name][0]
    env = {"GOOGLE_CLOUD_PROJECT": "project", variable: json.dumps(good) if isinstance(good, (list, dict, int, float)) else good}
    assert load_config(tmp_path / "absent", env)[name] == (tuple(good) if isinstance(good, list) else good)
    path = write_config(tmp_path, name, good)
    assert load_config(path, dict(env, **{variable: "INVALID"}))[name] == load_config(path, env)[name]
    assert load_config(write_config(tmp_path, name, "${SELECTED}"), dict(env, SELECTED=env[variable]))[name] == load_config(tmp_path / "absent", env)[name]
    for value in bad:
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, name, value), env)
        if isinstance(value, str) or isinstance(good, (list, dict, int, float)):
            with pytest.raises(ConfigError):
                load_config(tmp_path / "absent", dict(env, **{variable: json.dumps(value) if not isinstance(value, str) else value}))
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent", dict(env, **{variable: ""}))
@pytest.mark.parametrize("text", ["", "null", "[]", "x: secret", "auth: null", "auth: {secret: secret}", "auth: {client_secret: secret}", "GOOGLE_OAUTH_CLIENT_SECRET: secret", "model: a\nmodel: b", "model: &x a\nthinking: *x", "model: !custom secret", "[", "vertex.project: secret", "%YAML 1.1\n---\nmodel: yes", "auth: {<<: {mode: none}}"])
def test_ctr04_strict_errors_are_sanitized(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=r"^ERROR: invalid_config at (config|auth)\.$"):
        load_config(path, {"GOOGLE_CLOUD_PROJECT": "project"})
def test_ac_05_9_absence_example_and_read_failures(tmp_path, monkeypatch):
    env = {"GOOGLE_CLOUD_PROJECT": "project"}
    config = load_config(tmp_path / "absent", env)
    assert (config["model"], config["thinking"], config["default_language"]) == ("gemini-3.1-flash-lite", "low", "ja")
    assert load_config(Path(__file__).resolve().parents[2] / "config.example.yaml", env).values == config.values
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent", {})
    with pytest.raises(ConfigError):
        load_config(tmp_path, env)
    (tmp_path / "invalid").write_bytes(b"\xff")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "invalid", env)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(PermissionError("secret")))
    with pytest.raises(ConfigError, match=r"^ERROR: invalid_config at config\.$"):
        load_config(tmp_path / "file", env)
def test_ctr04_placeholders_empty_overrides_and_secrets(tmp_path):
    path = tmp_path / "config.yaml"
    env = {"GOOGLE_CLOUD_PROJECT": "project", "TERMS": "[]", "COPYEDITOR_PROTECTED_TERMS": "invalid", "GOOGLE_OAUTH_CLIENT_ID": "invalid", "COPYEDITOR_PRICING": "invalid"}
    path.write_text('protected_terms: ${TERMS}\nauth: {client_id: null}\npricing: {}')
    config = load_config(path, env)
    assert config["protected_terms"] == () and config["auth.client_id"] is None and not config["pricing"]
    for value in ("${MISSING}", "${GOOGLE_OAUTH_CLIENT_SECRET}", "${OAUTH_SIGNING_KEY}", "${TERMS:-x}"):
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, "protected_terms", value), env)
    path.write_text('auth: {mode: google, client_id: client, base_url: "https://example.com/", allowed_emails: ["A+b@example.com"]}')
    env = {"GOOGLE_CLOUD_PROJECT": "project", "GOOGLE_OAUTH_CLIENT_SECRET": "secret", "OAUTH_SIGNING_KEY": "鍵" * 11}
    config = load_config(path, env)
    assert config["auth.base_url"] == "https://example.com" and "secret" not in repr(config)
    for key in ("GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_SIGNING_KEY"):
        with pytest.raises(ConfigError):
            load_config(path, dict(env, **{key: ""}))
    with pytest.raises(ConfigError):
        load_config(path, dict(env, OAUTH_SIGNING_KEY="a" * 31))
    config.require_language({"ja"})
    with pytest.raises(ConfigError):
        config.require_language({"en"})
    with pytest.raises(TypeError):
        config.values["model"] = "changed"
    assert strict_yaml("value: yes")["value"] == "yes"

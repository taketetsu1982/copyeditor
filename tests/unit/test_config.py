import secrets

import pytest

from copyeditor.config import ConfigError, load_config


def google_env():
    return {"GOOGLE_CLOUD_PROJECT": "project", "COPYEDITOR_AUTH_MODE": "google",
            "BASE_URL": "https://service.example", "GOOGLE_OAUTH_CLIENT_ID": "client",
            "COPYEDITOR_ALLOWED_DOMAINS": '["example.com"]',
            "GOOGLE_OAUTH_CLIENT_SECRET": secrets.token_urlsafe(32),
            "OAUTH_SIGNING_KEY": secrets.token_urlsafe(32)}


def test_environment_defaults_and_model_override():
    config = load_config({"GOOGLE_CLOUD_PROJECT": "project"})
    assert config["vertex.project"] == "project" and config["vertex.location"] == "global"
    assert config["model"] == "gemini-3.7-flash" and config["auth.mode"] == "none"
    config = load_config({"GOOGLE_CLOUD_PROJECT": "other", "COPYEDITOR_MODEL": "custom", "GOOGLE_CLOUD_LOCATION": "us-central1"})
    assert config["vertex.project"] == "other" and config["model"] == "custom"
    assert config["vertex.location"] == "us-central1"


@pytest.mark.parametrize("environment", [{}, {"GOOGLE_CLOUD_PROJECT": " "}, {"GOOGLE_CLOUD_PROJECT": "p", "COPYEDITOR_AUTH_MODE": "wrong"}])
def test_invalid_required_configuration_fails(environment):
    with pytest.raises(ConfigError):
        load_config(environment)


@pytest.mark.parametrize("key", ["BASE_URL", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_SIGNING_KEY", "COPYEDITOR_ALLOWED_DOMAINS"])
def test_google_mode_requires_identity_configuration(key):
    environment = google_env()
    del environment[key]
    with pytest.raises(ConfigError):
        load_config(environment)


@pytest.mark.parametrize("value", ['"example.com"', '[1]', '{"domain":"example.com"}', 'example.com'])
def test_allowlist_requires_json_string_array(value):
    environment = google_env()
    environment["COPYEDITOR_ALLOWED_DOMAINS"] = value
    with pytest.raises(ConfigError):
        load_config(environment)


def test_email_only_login_and_secret_repr():
    environment = google_env()
    del environment["COPYEDITOR_ALLOWED_DOMAINS"]
    environment["COPYEDITOR_ALLOWED_EMAILS"] = '["person@example.com"]'
    config = load_config(environment)
    assert list(config["auth.allowed_emails"]) == ["person@example.com"]
    for key in ("GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_SIGNING_KEY"):
        assert environment[key] not in repr(config)

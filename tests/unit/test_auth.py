import asyncio
import json
import logging
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx2
import pytest
from key_value.aio._utils import managed_entry
from key_value.aio.stores.memory import MemoryStore

from copyeditor.auth import BoundedMemoryStore, CopyeditorOAuthProxy, IdentityVerifier, SCOPES, make_auth
from copyeditor.auth_boundary import disable_library_logging
from copyeditor.config import load_config


@pytest.fixture(autouse=True)
def restore_logging():
    previous = logging.root.manager.disable
    disable_library_logging()
    yield
    logging.disable(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["domain", "email", "upper-domain", "unverified", "truthy", "missing-hd",
    "wrong-hd", "bad-email", "email-case", "sub-mismatch", "empty-sub", "audience", "userinfo-error", "network-error",
    "missing-verified", "numeric-verified", "numeric-sub", "non-ascii", "missing-email", "invalid-json",
    "kelvin-email", "kelvin-hd", "ascii-k-domain", "invalid-hd", "email-untrusted-hd"])
async def test_ac_05_3_identity_through_google_verifier(case, capsys, caplog):
    info = {"sub": "synthetic-sub", "email": "A@example.com", "email_verified": True, "hd": "example.com"}
    patches = {"unverified": {"email_verified": False}, "truthy": {"email_verified": "true"},
               "missing-hd": {"hd": None}, "wrong-hd": {"hd": "other.com"}, "bad-email": {"email": "a@@example.com"},
               "email-case": {"email": "a@example.com", "hd": None}, "sub-mismatch": {"sub": "other"},
               "empty-sub": {"sub": ""}, "upper-domain": {"email": "A@EXAMPLE.COM", "hd": "EXAMPLE.COM"},
               "email": {"hd": None}, "missing-verified": {"email_verified": None},
               "numeric-verified": {"email_verified": 1}, "numeric-sub": {"sub": 1},
               "non-ascii": {"email": "é@example.com"}, "missing-email": {"email": None},
               "kelvin-email": {"email": "A@\u212a.example", "hd": "k.example"},
               "kelvin-hd": {"email": "A@k.example", "hd": "\u212a.example"},
               "ascii-k-domain": {"email": "A@K.EXAMPLE", "hd": "K.EXAMPLE"},
               "invalid-hd": {"hd": "example.com."}, "email-untrusted-hd": {"hd": "\u212a.example"}}
    info.update(patches.get(case, {}))
    def respond(request):
        if request.url.path == "/tokeninfo":
            return httpx2.Response(200, json={"aud": "wrong" if case == "audience" else "client",
                "sub": "synthetic-sub", "scope": " ".join(SCOPES), "expires_in": 900})
        if request.url.path == "/oauth2/v2/userinfo":
            return httpx2.Response(200, json={})
        assert request.url.host == "openidconnect.googleapis.com"
        if case == "network-error":
            raise httpx2.ConnectError("SYNTHETIC_PRIVATE_DETAIL")
        if case == "invalid-json":
            return httpx2.Response(200, content=b"{")
        return httpx2.Response(500 if case == "userinfo-error" else 200, json=info)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        verifier = IdentityVerifier(domains=["example.com", "k.example"],
                                    emails=["A@example.com"] if case in ("email", "email-case", "email-untrusted-hd") else [],
                                    audience="client", required_scopes=SCOPES, http_client=client)
        result = await verifier.verify_token("synthetic-token")
    assert (result is not None) == (case in ("domain", "email", "upper-domain", "ascii-k-domain", "email-untrusted-hd"))
    assert not caplog.records
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("domains,emails,hint", [(["example.com"], [], "example.com"),
    (["example.com", "other.com"], [], None), (["example.com"], ["A@example.com"], None), ([], ["A@example.com"], None)])
def test_ctr04_factory_settings_and_hint(tmp_path, domains, emails, hint, monkeypatch, capsys, caplog):
    config = load_config(tmp_path / "absent", {"GOOGLE_CLOUD_PROJECT": "test", "COPYEDITOR_AUTH_MODE": "google",
        "GOOGLE_OAUTH_CLIENT_ID": "client", "BASE_URL": "https://service.example",
        "COPYEDITOR_ALLOWED_DOMAINS": json.dumps(domains), "COPYEDITOR_ALLOWED_EMAILS": json.dumps(emails),
        "GOOGLE_OAUTH_CLIENT_SECRET": secrets.token_urlsafe(32), "OAUTH_SIGNING_KEY": secrets.token_urlsafe(32)})
    logging.disable(logging.NOTSET)
    original = CopyeditorOAuthProxy.__init__
    def construct(self, **kwargs):
        assert logging.root.manager.disable == logging.CRITICAL
        logging.critical("SYNTHETIC_PRIVATE_DETAIL")
        original(self, **kwargs)
    monkeypatch.setattr(CopyeditorOAuthProxy, "__init__", construct)
    proxy = make_auth(config)
    assert capsys.readouterr() == ("", "") and not caplog.records
    assert logging.root.manager.disable == logging.CRITICAL
    assert isinstance(proxy, CopyeditorOAuthProxy) and isinstance(proxy._client_storage, BoundedMemoryStore)
    assert proxy._extra_authorize_params.get("hd") == hint and "login_hint" not in proxy._extra_authorize_params
    assert proxy._fastmcp_access_token_expiry_seconds == 900 and proxy._require_authorization_consent is True
    assert proxy._cimd_manager is None and proxy._redirect_path == "/auth/callback"
    assert str(proxy.issuer_url).rstrip("/") == "https://service.example"
    assert str(proxy.resource_base_url).rstrip("/") == "https://service.example"
    assert proxy._upstream_authorization_endpoint == "https://accounts.google.com/o/oauth2/v2/auth"
    assert proxy._upstream_token_endpoint == "https://oauth2.googleapis.com/token"
    assert proxy._token_validator.required_scopes == SCOPES
    assert proxy._token_validator.audience == "client" and proxy._forward_resource is False


def test_ctr04_none_does_not_initialize_google(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Google initialization attempted")
    monkeypatch.setattr(IdentityVerifier, "__init__", forbidden)
    monkeypatch.setattr(CopyeditorOAuthProxy, "__init__", forbidden)
    monkeypatch.setattr(httpx2, "AsyncClient", forbidden)
    assert make_auth({"auth.mode": "none"}) is None


def test_ac_05_3_logging_entry_is_available_before_sdk_import():
    code = "from copyeditor.auth_boundary import disable_library_logging; import sys, logging; "
    code += "assert 'fastmcp' not in sys.modules; disable_library_logging(); logging.critical('SYNTHETIC_PRIVATE_DETAIL')"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={"PYTHONPATH": "src"})
    assert result.returncode == 0 and result.stdout == result.stderr == ""


@pytest.mark.asyncio
async def test_ac_05_3_capacity_updates_expiration_collections_and_race(monkeypatch):
    store = BoundedMemoryStore()
    for i in range(1999):
        await store.put(key=str(i), value={"v": 1}, collection="auth")
    original_put = MemoryStore._put_managed_entry
    async def yielding_put(self, **kwargs):
        await asyncio.sleep(0)
        await original_put(self, **kwargs)
    monkeypatch.setattr(MemoryStore, "_put_managed_entry", yielding_put)
    outcomes = await asyncio.gather(*(store.put(key=k, value={"v": 2}, collection="auth") for k in ("a", "b")),
                                    return_exceptions=True)
    assert sum(isinstance(x, ValueError) for x in outcomes) == 1
    assert len(await store._get_collection_keys(collection="auth")) == 2000
    await store.put(key="0", value={"v": 3}, collection="auth")
    assert await store.get(key="0", collection="auth") == {"v": 3}
    with pytest.raises(ValueError, match="^Authentication capacity reached$"):
        await store.put(key="overflow", value={}, collection="auth")
    await store.put(key="independent", value={}, collection="other")
    await store.put(key="0", value={}, collection="auth", ttl=1)
    future = datetime.now(timezone.utc) + timedelta(seconds=2)
    monkeypatch.setattr(managed_entry, "now", lambda: future)
    await store.put(key="replacement", value={}, collection="auth")
    assert await store.get(key="0", collection="auth") is None
    assert len(await store._get_collection_keys(collection="auth")) == 2000

import base64
import hashlib
import logging
import secrets
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from fastmcp.server.auth.oauth_proxy.models import OAuthTransaction
from mcp.server.auth.provider import TokenError
from starlette.applications import Starlette
from starlette.responses import Response

from copyeditor.auth import make_auth
from copyeditor.config import load_config

MARKER = "SYNTHETIC_PRIVATE_DETAIL"
CALLBACK = "https://client.example/callback?fixed=keep"
VERIFIER = "v" * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


@pytest_asyncio.fixture
async def auth(tmp_path, monkeypatch, capsys, caplog):
    previous = logging.root.manager.disable
    try:
        proxy = make_auth(load_config(tmp_path / "absent", {"GOOGLE_CLOUD_PROJECT": "test",
            "COPYEDITOR_AUTH_MODE": "google", "GOOGLE_OAUTH_CLIENT_ID": "client", "BASE_URL": "https://service.example",
            "COPYEDITOR_ALLOWED_DOMAINS": '["example.com"]', "GOOGLE_OAUTH_CLIENT_SECRET": secrets.token_urlsafe(32),
            "OAUTH_SIGNING_KEY": secrets.token_urlsafe(32)}))
        upstream = SimpleNamespace(fetch_token=AsyncMock(return_value={"access_token": "synthetic", "token_type": "Bearer"}),
                                   aclose=AsyncMock())
        monkeypatch.setattr(proxy, "_create_upstream_oauth_client", lambda: upstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=proxy.get_routes("/mcp"))),
                                    base_url="https://service.example") as client:
            response = await client.post("/register", json={"redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"})
            assert response.status_code == 201
            client_id = response.json()["client_id"]
            transaction = OAuthTransaction(txn_id="txn", client_id=client_id, client_redirect_uri=CALLBACK,
                client_state="saved", code_challenge=CHALLENGE, code_challenge_method="S256", scopes=[],
                created_at=time.time(), consent_token="consent")
            await proxy._transaction_store.put(key="txn", value=transaction, ttl=600)
            cookie = Response()
            proxy._write_consent_bindings(cookie, {"txn": "consent"})
            client.cookies.extract_cookies(httpx.Response(200, headers=cookie.headers, request=httpx.Request("GET", str(client.base_url))))
            yield proxy, client, upstream, client_id
        assert MARKER not in repr(capsys.readouterr()) and not caplog.records
    finally:
        logging.disable(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["callback", "registration", "token", "callback-exception"])
async def test_ac_05_3_sdk_error_routes_remove_details(auth, kind, monkeypatch):
    proxy, client, upstream, client_id = auth
    if kind == "callback":
        response = await client.get("/auth/callback", params={"state": "txn", "error": "access_denied", "error_description": MARKER})
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query == {"fixed": ["keep"], "state": ["saved"], "iss": [str(proxy.issuer_url)],
                         "error": ["access_denied"], "error_description": ["Authentication failed."]}
        assert response.headers["location"].startswith(CALLBACK + "&")
        assert response.status_code == 302
    elif kind == "registration":
        response = await client.post("/register", json={"redirect_uris": [CALLBACK], "scope": MARKER})
        assert response.status_code == 400
    elif kind == "callback-exception":
        upstream.fetch_token.side_effect = ValueError(MARKER)
        response = await client.get("/auth/callback", params={"state": "txn", "code": "synthetic"})
        upstream.fetch_token.assert_awaited_once()
        upstream.aclose.assert_awaited_once()
        assert response.status_code == 500
    else:
        normal = await client.get("/auth/callback", params={"state": "txn", "code": "synthetic"})
        assert normal.status_code == 302
        target = urlsplit(normal.headers["location"])
        query = parse_qs(target.query)
        assert target.scheme == "https" and target.netloc == "client.example" and target.path == "/callback"
        assert query["fixed"] == ["keep"] and query["state"] == ["saved"] and query["iss"] == [str(proxy.issuer_url)]
        code = query["code"][0]
        stored = await proxy._code_store.get(key=code)
        assert stored.code_challenge == CHALLENGE and stored.code_challenge_method == "S256"
        exchange = AsyncMock(side_effect=TokenError("invalid_grant", MARKER))
        monkeypatch.setattr(proxy, "exchange_authorization_code", exchange)
        rejected = await client.post("/token", data={"client_id": client_id, "grant_type": "authorization_code",
            "code": code, "redirect_uri": CALLBACK, "code_verifier": "wrong"})
        assert rejected.status_code == 401
        exchange.assert_not_awaited()
        response = await client.post("/token", data={"client_id": client_id, "grant_type": "authorization_code",
            "code": code, "redirect_uri": CALLBACK, "code_verifier": VERIFIER})
        exchange.assert_awaited_once()
        assert response.status_code == 401 and response.json()["error"] == "invalid_grant"
    assert MARKER not in response.text and MARKER not in repr(response.headers)
    assert "Authentication failed." in response.text

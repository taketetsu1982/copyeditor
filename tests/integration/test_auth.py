import base64
import hashlib
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import httpx2
import pytest
import pytest_asyncio
from key_value.aio._utils import managed_entry, time_to_live
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from starlette.applications import Starlette
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import Response
from starlette.routing import Route

from copyeditor.auth import SCOPES, make_auth
from copyeditor.config import load_config

ORIGIN = "https://service.example"
CALLBACK = "https://client.example/callback?fixed=keep"
VERIFIER = "v" * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
MARKER = "SYNTHETIC_PRIVATE_DETAIL"


class LoopbackTransport(httpx2.AsyncHTTPTransport):
    def __init__(self, origin):
        super().__init__()
        self.origin = origin

    async def handle_async_request(self, request):
        allowed = {("oauth2.googleapis.com", "/tokeninfo"),
                   ("www.googleapis.com", "/oauth2/v2/userinfo"),
                   ("openidconnect.googleapis.com", "/v1/userinfo")}
        if request.url.scheme != "https" or (request.url.host, request.url.path) not in allowed:
            raise RuntimeError("Unexpected upstream destination")
        request.url = httpx2.URL(self.origin + request.url.path).copy_with(query=request.url.query)
        return await super().handle_async_request(request)


class Clock:
    def __init__(self, monkeypatch):
        self.real_time, self.offset = time.time, 0
        monkeypatch.setattr(time, "time", self.now)
        for module in (time_to_live, managed_entry):
            monkeypatch.setattr(module, "now", lambda: datetime.fromtimestamp(self.now(), timezone.utc))
            monkeypatch.setattr(module, "now_plus", lambda seconds: time_to_live.now() + timedelta(seconds=seconds))

    def now(self):
        return self.real_time() + self.offset

    def advance(self, seconds):
        self.offset += seconds


@pytest_asyncio.fixture
async def oauth(tmp_path, monkeypatch):
    previous = logging.root.manager.disable
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(variable, raising=False)
    state = SimpleNamespace(calls=[], responses={}, clock=Clock(monkeypatch))

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond({})

        def do_POST(self):
            self.respond(parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()))

        def respond(self, form):
            target = urlsplit(self.path)
            query = parse_qs(target.query)
            state.calls.append((target.path, query, form, self.headers.get("Authorization")))
            if target.path == "/authorize":
                self.send_response(302)
                self.send_header("Location", ORIGIN + "/auth/callback?" + urlencode(
                    {"code": "synthetic-code", "state": query["state"][0]}))
                self.end_headers()
                return
            refreshed = form.get("grant_type") == ["refresh_token"]
            identity = {"sub": MARKER + "-sub", "email": MARKER + "@example.com",
                        "email_verified": True, "hd": "example.com"}
            defaults = {"/token": {"access_token": MARKER + ("-new" if refreshed else "-old"),
                                   "refresh_token": MARKER + "-refresh", "token_type": "Bearer",
                                   "expires_in": 3600, "scope": " ".join(SCOPES)},
                        "/tokeninfo": {**identity, "aud": "client", "scope": " ".join(SCOPES), "expires_in": 3600},
                        "/oauth2/v2/userinfo": identity, "/v1/userinfo": identity}
            status, body = state.responses.get(target.path, (200, defaults.get(target.path, {})))
            encoded = json.dumps(body).encode()
            self.send_response(status if target.path in defaults else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.origin = f"http://127.0.0.1:{server.server_port}"
    try:
        state.config = load_config(tmp_path / "absent", {"GOOGLE_CLOUD_PROJECT": "test",
            "COPYEDITOR_AUTH_MODE": "google", "BASE_URL": ORIGIN, "GOOGLE_OAUTH_CLIENT_ID": "client",
            "COPYEDITOR_ALLOWED_DOMAINS": '["example.com"]', "GOOGLE_OAUTH_CLIENT_SECRET": secrets.token_urlsafe(32),
            "OAUTH_SIGNING_KEY": secrets.token_urlsafe(32)})
        state.proxy = make_auth(state.config)
        state.proxy._upstream_authorization_endpoint = state.origin + "/authorize"
        state.proxy._upstream_token_endpoint = state.origin + "/token"
        async with httpx2.AsyncClient(transport=LoopbackTransport(state.origin), trust_env=False) as verifier_client:
            state.proxy._token_validator._http_client = verifier_client
            protected = RequireAuthMiddleware(Response("accepted"), required_scopes=SCOPES)
            app = Starlette(routes=[*state.proxy.get_routes("/mcp"), Route("/protected", protected)])
            app.add_middleware(AuthenticationMiddleware, backend=BearerAuthBackend(state.proxy))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                async with httpx.AsyncClient(trust_env=False) as upstream_client:
                    state.client, state.upstream_client = client, upstream_client
                    yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        logging.disable(previous)
        assert not thread.is_alive()
        assert server.socket.fileno() == -1


async def authorize(oauth):
    client = oauth.client
    registration = await client.post("/register", json={"redirect_uris": [CALLBACK],
        "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
        "scope": " ".join(SCOPES)})
    assert registration.status_code == 201
    client_id = registration.json()["client_id"]
    response = await client.get("/authorize", params={"client_id": client_id, "response_type": "code",
        "redirect_uri": CALLBACK, "state": "saved-client-state", "scope": " ".join(SCOPES),
        "code_challenge": CHALLENGE, "code_challenge_method": "S256"})
    assert response.status_code == 302
    consent_url = response.headers["location"]
    assert consent_url.startswith(ORIGIN + "/")
    consent = await client.get(consent_url)
    assert consent.status_code == 200
    fields = {name: re.search(r'name="' + name + r'"\s+value="([^"]+)"', consent.text)[1]
              for name in ("txn_id", "csrf_token")}
    approved = await client.post(urlsplit(consent_url).path, data={**fields, "action": "approve"})
    assert approved.status_code == 302
    upstream_url = approved.headers["location"]
    assert upstream_url.startswith(oauth.origin + "/authorize?")
    query = parse_qs(urlsplit(upstream_url).query)
    assert query["state"] == [fields["txn_id"]]
    assert query["redirect_uri"] == [ORIGIN + "/auth/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    return client_id, upstream_url


async def callback(oauth, upstream_url):
    upstream = await oauth.upstream_client.get(upstream_url)
    assert upstream.status_code == 302
    location = upstream.headers["location"]
    assert location.startswith(ORIGIN + "/auth/callback?")
    response = await oauth.client.get(location)
    assert response.status_code == 302
    target = urlsplit(response.headers["location"])
    assert (target.scheme, target.netloc, target.path) == ("https", "client.example", "/callback")
    query = parse_qs(target.query)
    assert query["fixed"] == ["keep"] and query["state"] == ["saved-client-state"]
    assert query["iss"] == [str(oauth.proxy.issuer_url)]
    return query["code"][0]


async def exchange(oauth, client_id, code, verifier=VERIFIER):
    return await oauth.client.post("/token", data={"client_id": client_id, "grant_type": "authorization_code",
        "code": code, "redirect_uri": CALLBACK, "code_verifier": verifier})


@pytest.mark.asyncio
async def test_ac_05_3_ctr04_real_oauth_round_trip(oauth, capsys):
    client_id, upstream_url = await authorize(oauth)
    code = await callback(oauth, upstream_url)
    exchanged = await exchange(oauth, client_id, code)
    assert exchanged.status_code == 200
    tokens = exchanged.json()
    assert tokens["expires_in"] == 900
    first_expiry = oauth.proxy.jwt_issuer.verify_token(tokens["access_token"])["exp"]
    verifier = oauth.proxy._token_validator
    for refresh in (False, True):
        if refresh:
            oauth.clock.advance(1)
            response = await oauth.client.post("/token", data={"client_id": client_id,
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
            assert response.status_code == 200
            tokens = response.json()
            assert oauth.proxy.jwt_issuer.verify_token(tokens["access_token"])["exp"] > first_expiry
        start = len(oauth.calls)
        response = await oauth.client.get("/protected", headers={"Authorization": "Bearer " + tokens["access_token"]})
        assert response.status_code == 200 and response.text == "accepted"
        assert oauth.proxy._token_validator is verifier
        calls = oauth.calls[start:]
        assert [c[0] for c in calls] == ["/tokeninfo", "/oauth2/v2/userinfo", "/v1/userinfo"]
        expected = MARKER + ("-new" if refresh else "-old")
        assert bool(calls[0][1].get("access_token") == [expected])
        assert all(c[3] == "Bearer " + expected for c in calls[1:])
    upstream_exchange = next(c[2] for c in oauth.calls if c[0] == "/token")
    proof = base64.urlsafe_b64encode(hashlib.sha256(upstream_exchange["code_verifier"][0].encode()).digest())
    assert bool(proof.decode().rstrip("=") == parse_qs(urlsplit(upstream_url).query)["code_challenge"][0])
    grants = [c[2]["grant_type"] for c in oauth.calls if c[0] == "/token"]
    assert grants == [["authorization_code"], ["refresh_token"]]
    assert bool(MARKER not in repr(capsys.readouterr()))

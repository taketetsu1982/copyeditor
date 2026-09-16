import json
from urllib.parse import parse_qs, urlsplit

import pytest

from copyeditor.auth_boundary import DESCRIPTION, REDIRECT_ERRORS, wrap_auth_app

MARKER = "SYNTHETIC_PRIVATE_DETAIL"


async def run_response(status=400, headers=(), chunks=(MARKER.encode(),), fail=False, send_fail=False):
    received = []
    async def send(message):
        received.append(message)
        if send_fail:
            raise ValueError(MARKER)
    async def app(scope, receive, output):
        await output({"type": "http.response.start", "status": status, "headers": list(headers)})
        for chunk in chunks:
            await output({"type": "http.response.body", "body": chunk, "more_body": True})
            assert bool(received) == (status < 400 and not any(k == b"location" and b"error=" in v for k, v in headers))
        if fail:
            raise ValueError(MARKER)
        await output({"type": "http.response.body", "body": b"", "more_body": False})
    await wrap_auth_app(app)({"type": "http"}, None, send)
    return received


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,code", [(b"text/html", "invalid_request"), (b"application/json", "invalid_grant"),
    (b"application/json", MARKER), (b"application/json", None)])
async def test_ac_05_3_error_body_and_headers_are_fixed(kind, code, capsys):
    raw = json.dumps({"error": code, "error_description": MARKER, "error_uri": MARKER}).encode()
    result = await run_response(headers=[(b"content-type", kind), (b"x-detail", MARKER.encode())], chunks=(raw[:8], raw[8:]))
    body, headers = result[1]["body"], dict(result[0]["headers"])
    assert MARKER not in repr(result) and int(headers[b"content-length"]) == len(body)
    if kind == b"text/html":
        assert body == DESCRIPTION.encode()
    else:
        assert json.loads(body) == {"error": code if code == "invalid_grant" else "server_error", "error_description": DESCRIPTION}
    assert capsys.readouterr() == ("", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [*sorted(REDIRECT_ERRORS), MARKER])
async def test_ctr04_error_redirect_keeps_sdk_destination_state_and_issuer(code):
    url = f"https://client.example/callback?fixed=keep&state=saved&iss=issuer&error={code}&error_description={MARKER}&error_uri={MARKER}"
    result = await run_response(302, [(b"location", url.encode())])
    target = urlsplit(dict(result[0]["headers"])[b"location"].decode())
    assert result[0]["status"] == 302 and target.netloc == "client.example" and target.path == "/callback"
    assert parse_qs(target.query) == {"fixed": ["keep"], "state": ["saved"], "iss": ["issuer"],
        "error": [code if code in REDIRECT_ERRORS else "server_error"], "error_description": [DESCRIPTION]}
    assert MARKER not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["/relative?error=x", "https://[broken?error=x", "https://c/?error=x&error=y",
    "https://c/?error=x%ZZ", "https://c/?error=x#fragment", "https://u:p@c/?error=x", "https://c/\r?error=x",
    "javascript:alert(1)?error=x", "data:text/html,test?error=x", "https://c:invalid/?error=x"])
async def test_ac_05_3_invalid_redirect_closes_locally(url):
    result = await run_response(302, [(b"location", url.encode())])
    assert result[0]["status"] == 500 and b"location" not in dict(result[0]["headers"])
    assert json.loads(result[1]["body"])["error_description"] == DESCRIPTION


@pytest.mark.asyncio
@pytest.mark.parametrize("status,target", [(200, None), (200, b"/created"),
    (200, b"https://c/?code=code&state=saved&code_challenge=pkce&code_challenge_method=S256"),
    (302, b"https://c/?code=code&state=saved&code_challenge=pkce&code_challenge_method=S256")])
async def test_ctr04_success_stream_and_redirect_are_unchanged(status, target):
    headers = [] if target is None else [(b"location", target)]
    result = await run_response(status, headers, chunks=(b"one", b"two"))
    assert result[0] == {"type": "http.response.start", "status": status, "headers": headers}
    assert [m["body"] for m in result[1:]] == [b"one", b"two", b""]


@pytest.mark.asyncio
async def test_ac_05_3_exceptions_before_and_after_commit_do_not_leak():
    result = await run_response(fail=True)
    assert result[0]["status"] == 500 and MARKER not in repr(result)
    for options in ({"send_fail": True}, {"fail": True, "send_fail": True}, {"status": 200, "chunks": (), "fail": True}):
        with pytest.raises(RuntimeError, match=r"^Authentication failed\.$"):
            await run_response(**options)


@pytest.mark.asyncio
async def test_ctr04_duplicate_location_and_malformed_json_fail_closed():
    result = await run_response(302, [(b"location", b"https://c/?error=x")] * 2)
    assert result[0]["status"] == 500 and b"location" not in dict(result[0]["headers"])
    result = await run_response(headers=[(b"content-type", b"application/json")], chunks=(b"{", MARKER.encode()))
    assert MARKER not in repr(result) and json.loads(result[1]["body"])["error"] == "server_error"


@pytest.mark.asyncio
async def test_ctr04_non_http_scope_is_delegated():
    calls = []
    async def app(scope, receive, send):
        calls.append(scope)
    await wrap_auth_app(app)({"type": "lifespan"}, None, None)
    assert calls == [{"type": "lifespan"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201, 400, 500])
async def test_ac_05_3_error_location_outside_redirect_closes_locally(status):
    headers = [(b"location", f"https://c/?error=access_denied&error_description={MARKER}".encode()),
               (b"x-detail", MARKER.encode())]
    result = await run_response(status, headers)
    assert result[0]["status"] == 500 and MARKER not in repr(result)
    assert b"location" not in dict(result[0]["headers"])
    assert json.loads(result[1]["body"]) == {"error": "server_error", "error_description": DESCRIPTION}

import logging
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def disable_library_logging():
    logging.disable(logging.CRITICAL)


DESCRIPTION = "Authentication failed."
REDIRECT_ERRORS = frozenset("invalid_request unauthorized_client access_denied unsupported_response_type invalid_scope server_error temporarily_unavailable".split())
JSON_ERRORS = REDIRECT_ERRORS | {"invalid_client", "invalid_grant", "unsupported_grant_type", "invalid_client_metadata", "invalid_redirect_uri"}


def error_location(value):
    if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError()
    url = urlsplit(value)
    if not url.scheme or url.scheme in ("javascript", "data", "vbscript") or url.fragment or url.username or url.password or "\\" in value:
        raise ValueError()
    if url.scheme in ("http", "https") and (not url.hostname or url.port == 0):
        raise ValueError()
    query = parse_qsl(url.query, keep_blank_values=True, errors="strict")
    errors = [v for k, v in query if k == "error"]
    if not errors:
        return None
    if len(errors) != 1:
        raise ValueError()
    code = errors[0] if errors[0] in REDIRECT_ERRORS else "server_error"
    query = [(k, v) for k, v in query if k not in ("error", "error_description", "error_uri")]
    query += [("error", code), ("error_description", DESCRIPTION)]
    return urlunsplit(url._replace(query=urlencode(query)))


def wrap_auth_app(app):
    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        start, buffered, location, committed = None, None, None, False

        async def emit(message):
            nonlocal committed
            committed = True
            try:
                await send(message)
            except Exception:
                raise RuntimeError(DESCRIPTION) from None

        async def intercept(message):
            nonlocal start, buffered, location
            if message["type"] == "http.response.start":
                start = message
                headers = message.get("headers", [])
                if 300 <= message["status"] < 400:
                    locations = [v for k, v in headers if k.lower() == b"location"]
                    if len(locations) > 1:
                        raise ValueError()
                    if locations:
                        location = error_location(locations[0].decode("ascii"))
                if message["status"] >= 400 or location is not None:
                    buffered = bytearray()
                    return
            if buffered is not None and message["type"] == "http.response.body":
                buffered.extend(message.get("body", b""))
                return
            await emit(message)

        try:
            await app(scope, receive, intercept)
            if buffered is None:
                return
            content_type = dict(start.get("headers", [])).get(b"content-type", b"").lower()
            code = "server_error"
            if b"json" in content_type:
                try:
                    candidate = json.loads(buffered).get("error")
                    code = candidate if isinstance(candidate, str) and candidate in JSON_ERRORS else code
                except (ValueError, AttributeError):
                    pass
            body = DESCRIPTION.encode() if b"text/html" in content_type else json.dumps({"error": code, "error_description": DESCRIPTION}).encode()
            headers = [(b"content-type", b"text/html; charset=utf-8" if b"text/html" in content_type else b"application/json"),
                       (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]
            if location is not None:
                headers.append((b"location", location.encode("ascii")))
            await emit({"type": "http.response.start", "status": start["status"], "headers": headers})
            await emit({"type": "http.response.body", "body": body})
        except Exception:
            # A failed send or committed success cannot be replaced by a second response.
            if committed:
                raise RuntimeError(DESCRIPTION) from None
            body = json.dumps({"error": "server_error", "error_description": DESCRIPTION}).encode()
            await emit({"type": "http.response.start", "status": 500, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await emit({"type": "http.response.body", "body": body})
    return wrapped

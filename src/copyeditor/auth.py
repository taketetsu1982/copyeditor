import asyncio
from contextlib import nullcontext

import httpx2
from fastmcp.server.auth import OAuthProxy
from fastmcp.server.auth.providers.google import GoogleTokenVerifier
from key_value.aio.stores.memory import MemoryStore

from copyeditor.auth_boundary import disable_library_logging
from copyeditor.config import email

SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]


class BoundedMemoryStore(MemoryStore):
    def __init__(self):
        super().__init__(max_entries_per_collection=2000)
        self._write_lock = asyncio.Lock()

    async def _get_collection_keys(self, *, collection, limit=None):
        keys = await super()._get_collection_keys(collection=collection)
        live = []
        # MemoryStore's cache timer does not guarantee ManagedEntry TTL cleanup.
        for key in keys:
            entry = await self._get_managed_entry(key=key, collection=collection)
            if entry is not None and not entry.is_expired:
                live.append(key)
            else:
                await self._delete_managed_entry(key=key, collection=collection)
        return live if limit is None else live[:limit]

    async def _put_managed_entry(self, *, key, collection, managed_entry):
        async with self._write_lock:
            existing = await self._get_managed_entry(key=key, collection=collection)
            keys = await self._get_collection_keys(collection=collection)
            if existing is None and len(keys) >= 2000:
                raise ValueError("Authentication capacity reached")
            await super()._put_managed_entry(key=key, collection=collection, managed_entry=managed_entry)


class IdentityVerifier(GoogleTokenVerifier):
    def __init__(self, *, domains, emails, **kwargs):
        super().__init__(**kwargs)
        self.domains, self.emails = tuple(domains), tuple(emails)

    async def verify_token(self, token):
        try:
            verified = await super().verify_token(token)
            if verified is None:
                return None
            async with (nullcontext(self._http_client) if self._http_client is not None
                        else httpx2.AsyncClient(timeout=self.timeout_seconds)) as client:
                response = await client.get("https://openidconnect.googleapis.com/v1/userinfo",
                                            headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                info = response.json()
            sub, address, hd = info.get("sub"), info.get("email"), info.get("hd")
            if not isinstance(sub, str) or not sub or sub != verified.claims.get("sub"):
                return None
            if info.get("email_verified") is not True or not isinstance(address, str):
                return None
            local, separator, domain = address.rpartition("@")
            if not separator or not email(local + "@" + domain.lower()):
                return None
            domain_allowed = isinstance(hd, str) and hd.lower() == domain.lower() in self.domains
            if address not in self.emails and not domain_allowed:
                return None
            # SDK profile fallbacks do not establish verified UserInfo identity.
            verified.claims = {key: info[key] for key in ("sub", "email", "email_verified", "hd") if key in info}
            return verified
        except Exception:
            return None


class CopyeditorOAuthProxy(OAuthProxy):
    pass


def make_auth(config):
    if config["auth.mode"] == "none":
        return None
    disable_library_logging()
    domains, emails = config["auth.allowed_domains"], config["auth.allowed_emails"]
    params = {"access_type": "offline", "prompt": "consent"}
    if len(domains) == 1 and not emails:
        params["hd"] = domains[0]
    return CopyeditorOAuthProxy(
        upstream_authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        upstream_token_endpoint="https://oauth2.googleapis.com/token",
        upstream_client_id=config["auth.client_id"],
        upstream_client_secret=config.secrets["GOOGLE_OAUTH_CLIENT_SECRET"],
        token_verifier=IdentityVerifier(domains=domains, emails=emails,
                                       audience=config["auth.client_id"], required_scopes=SCOPES),
        base_url=config["auth.base_url"], issuer_url=config["auth.base_url"],
        resource_base_url=config["auth.base_url"], redirect_path="/auth/callback",
        valid_scopes=SCOPES, extra_authorize_params=params, forward_resource=False,
        jwt_signing_key=config.secrets["OAUTH_SIGNING_KEY"],
        client_storage=BoundedMemoryStore(), fastmcp_access_token_expiry_seconds=900,
        require_authorization_consent=True, enable_cimd=False,
    )

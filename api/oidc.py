"""Authorization-code OIDC with PKCE, nonce, a pinned issuer and signed MFA claims."""
import base64
from dataclasses import dataclass
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlencode

import jwt

from integrations.http import https_url, request_json, IntegrationError


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    mfa_claim: str
    mfa_value: str
    tenant: str = ""

    @classmethod
    def from_env(cls):
        if not os.getenv("ATEZAIN_OIDC_ISSUER"):
            return None
        config = cls(*(os.getenv("ATEZAIN_OIDC_" + key, "") for key in
                       ("ISSUER", "CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI", "MFA_CLAIM", "MFA_VALUE", "TENANT")))
        https_url(config.issuer)
        https_url(config.redirect_uri)
        if not all((config.client_id, config.client_secret, config.mfa_value)) or config.mfa_claim not in {"acr", "acrs", "amr"}:
            raise ValueError("OIDC requires client credentials and an explicit signed MFA claim/value")
        if any(part in config.issuer.split("/") for part in ("common", "organizations", "consumers")):
            raise ValueError("OIDC requires a tenant-specific issuer")
        return config


class OIDC:
    def __init__(self, config, transport=request_json):
        self.config, self.transport = config, transport
        self._metadata, self._keys = None, None
        self._metadata_at = self._keys_at = 0

    def metadata(self):
        if self._metadata is None or time.time() - self._metadata_at > 300:
            data = self.transport("GET", self.config.issuer.rstrip("/") + "/.well-known/openid-configuration")
            if data.get("issuer") != self.config.issuer:
                raise ValueError("discovery issuer mismatch")
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                https_url(data[key])
            self._metadata, self._metadata_at = data, time.time()
        return self._metadata

    def start(self, store, binding):
        nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        state = store.begin(binding, {"kind": "oidc", "nonce": nonce, "verifier": verifier})
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return self.metadata()["authorization_endpoint"] + "?" + urlencode({
            "response_type": "code", "response_mode": "query", "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri, "scope": "openid", "state": state,
            "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "max_age": "300",
            "claims": json.dumps({"id_token": {self.config.mfa_claim: {"essential": True, "values": [self.config.mfa_value]}}})})

    def validate(self, token, nonce):
        if not isinstance(token, str) or len(token) > 16000:
            raise ValueError("invalid identity token")
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("invalid signing algorithm")
        for attempt in range(2):
            if self._keys is None or time.time() - self._keys_at > 300 or attempt:
                self._keys = self.transport("GET", self.metadata()["jwks_uri"])["keys"]
                self._keys_at = time.time()
            keys = [k for k in self._keys if k.get("kid") == header["kid"] and k.get("kty") == "RSA"
                    and k.get("use", "sig") == "sig" and k.get("alg", "RS256") == "RS256"]
            if len(keys) == 1:
                break
        if len(keys) != 1:
            raise ValueError("unknown signing key")
        claims = jwt.decode(token, jwt.PyJWK.from_dict(keys[0]).key, algorithms=["RS256"],
            audience=self.config.client_id, issuer=self.config.issuer,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce", "auth_time"]})
        if claims["nonce"] != nonce or not claims["sub"] or len(claims["sub"]) > 255:
            raise ValueError("invalid subject or nonce")
        if claims.get("azp", self.config.client_id) != self.config.client_id or (isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and "azp" not in claims):
            raise ValueError("invalid authorized party")
        now = time.time()
        if not isinstance(claims["auth_time"], (int, float)) or not now-330 <= claims["auth_time"] <= now+30:
            raise ValueError("fresh authentication required")
        if self.config.tenant and claims.get("tid") != self.config.tenant:
            raise ValueError("wrong tenant")
        mfa = claims.get(self.config.mfa_claim)
        if self.config.mfa_value not in (mfa if isinstance(mfa, list) else [mfa]):
            raise ValueError("required authentication assurance missing")
        return claims

    def finish(self, store, state, binding, code):
        attempt = store.consume(state, binding)
        if attempt.get("kind") != "oidc" or not code or len(code) > 8000:
            raise ValueError("invalid callback")
        tokens = self.transport("POST", self.metadata()["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "client_id": self.config.client_id,
            "client_secret": self.config.client_secret, "redirect_uri": self.config.redirect_uri,
            "code_verifier": attempt["verifier"]})
        return store.issue(self.validate(tokens.get("id_token"), attempt["nonce"]))

"""Cryptographic identity, cookie CSRF, membership and callback replay regression tests."""
import base64
import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from api.oidc import OIDC, OIDCConfig
from api.auth import Sessions
from tests.test_api import client, apimod, session, upload, assist


@pytest.fixture
def provider():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="key-1", alg="RS256", use="sig")
    config = OIDCConfig("https://id.example/tenant", "client", "secret", "https://app.example/auth/callback", "acrs", "c1", "tenant")
    claims = dict(iss=config.issuer, sub="finance-123", tid="tenant", aud="client", nonce="nonce",
                  iat=int(time.time()), exp=int(time.time()+600), auth_time=int(time.time()), acrs=["c1"])
    def transport(method, url, **kwargs):
        if url.endswith("openid-configuration"):
            return dict(issuer=config.issuer, authorization_endpoint="https://id.example/authorize", token_endpoint="https://id.example/token", jwks_uri="https://id.example/keys")
        if url.endswith("keys"):
            return {"keys": [jwk]}
        return {"id_token": jwt.encode(claims, key, algorithm="RS256", headers={"kid": "key-1"})}
    return OIDC(config, transport), key, claims


@pytest.mark.parametrize("field,value", [("iss", "https://other.example"), ("aud", "other"), ("tid", "other"),
    ("sub", ""), ("exp", 1), ("iat", 9999999999), ("nonce", "other"), ("acrs", []), ("auth_time", 1),
    ("azp", "other"), ("aud", ["client", "other"])])
def test_wrong_signed_identity_is_rejected(provider, field, value):
    oidc, key, claims = provider
    token = jwt.encode({**claims, field: value}, key, algorithm="RS256", headers={"kid": "key-1"})
    with pytest.raises((ValueError, jwt.PyJWTError)):
        oidc.validate(token, "nonce")


def test_unsigned_identity_and_attacker_key_are_rejected(provider):
    oidc, key, claims = provider
    for token in (jwt.encode(claims, "", algorithm="none"), jwt.encode(claims, "x"*32, algorithm="HS256", headers={"kid":"key-1"}),
                  jwt.encode(claims, rsa.generate_private_key(public_exponent=65537,key_size=2048), algorithm="RS256", headers={"kid":"key-1"})):
        with pytest.raises((ValueError, jwt.PyJWTError)):
            oidc.validate(token, "nonce")


def test_callback_pkce_nonce_binding_single_use_and_restart(provider, tmp_path):
    oidc, _, claims = provider
    sessions = Sessions(tmp_path / "sessions.db")
    url = oidc.start(sessions.identity, "browser")
    query = parse_qs(urlsplit(url).query)
    state = query["state"][0]
    saved = sessions._exec("SELECT payload FROM oauth_attempts").fetchone()[0]
    attempt = json.loads(saved)
    import hashlib
    assert query["code_challenge"][0] == base64.urlsafe_b64encode(hashlib.sha256(attempt["verifier"].encode()).digest()).rstrip(b"=").decode()
    claims["nonce"] = attempt["nonce"]
    reopened = Sessions(tmp_path / "sessions.db")
    with pytest.raises(ValueError):
        oidc.finish(reopened.identity, state, "different-browser", "code")
    token = oidc.finish(reopened.identity, state, "browser", "code")
    assert reopened.identity.login(token)["subject"] == "finance-123"
    with pytest.raises(ValueError):
        oidc.finish(reopened.identity, state, "browser", "code")
    assert "secret" not in url


def test_verified_access_cannot_cross_workspace_and_deprovision_is_immediate(client, provider, monkeypatch):
    oidc, _, claims = provider
    sid, owner = session(client); upload(client, sid, owner)
    other, _ = session(client)
    identity = apimod.sessions.identity
    identity.member(sid, claims["iss"], claims["sub"], "reviewer", True, "operator")
    token = identity.issue(claims)
    monkeypatch.setattr(apimod.sessions, "oidc_only", True)
    headers = {"Authorization": "Bearer " + token}
    assert client.get(f"/sessions/{sid}/records", headers=owner).status_code == 401
    assert client.get(f"/sessions/{other}/records", headers=headers).status_code == 401
    proposals = assist(client, sid, headers).json()["proposals"]
    held = next(p for p in proposals if p["status"] == "held")
    decision = client.post(f"/sessions/{sid}/proposals/{held['id']}/decide", headers=headers, json={"approve": True})
    assert decision.json()["proposal"]["decided_by"].startswith("human:oidc:")
    assert upload(client, sid, headers).status_code == 403
    identity.member(sid, claims["iss"], claims["sub"], "reviewer", False, "operator")
    assert client.get(f"/sessions/{sid}/records", headers=headers).status_code == 401
    identity.member(sid, claims["iss"], claims["sub"], "reviewer", True, "operator")
    assert client.get(f"/sessions/{sid}/records", headers=headers).status_code == 401


def test_browser_session_requires_csrf_and_logout_revokes(provider, monkeypatch):
    oidc, _, claims = provider
    monkeypatch.setattr(apimod, "oidc", oidc)
    token = apimod.sessions.identity.issue(claims)
    with TestClient(apimod.app, base_url="https://app.example") as browser:
        browser.cookies.set("__Host-atezain", token)
        me = browser.get("/auth/me").json()
        assert browser.post("/auth/logout").status_code == 403
        assert browser.post("/auth/logout", headers={"X-CSRF-Token": me["csrf"]}).status_code == 200
        assert apimod.sessions.identity.login(token) is None
        browser.cookies.set("__Host-atezain", token)
        assert browser.post("/auth/logout").status_code == 200


def test_login_cookie_is_secure_and_provider_errors_are_sanitized(provider, monkeypatch):
    oidc, _, _ = provider
    monkeypatch.setattr(apimod, "oidc", oidc)
    with TestClient(apimod.app, base_url="https://app.example") as browser:
        response = browser.get("/auth/login", follow_redirects=False)
        assert response.status_code == 303
        assert all(word in response.headers["set-cookie"] for word in ("Secure", "HttpOnly", "SameSite=lax"))
        bad = browser.get("/auth/callback?state=bad&code=SECRET_DO_NOT_REFLECT")
        assert bad.status_code == 401 and "SECRET_DO_NOT_REFLECT" not in bad.text


def test_signing_key_rollover_and_missing_required_claim(provider):
    oidc,key,claims=provider
    good=jwt.encode(claims,key,algorithm="RS256",headers={"kid":"key-1"})
    assert oidc.validate(good,"nonce")["sub"]==claims["sub"]
    new_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    new_jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(new_key.public_key()));new_jwk.update(kid="key-2",use="sig",alg="RS256")
    old_transport=oidc.transport
    oidc.transport=lambda method,url,**kwargs: {"keys":[new_jwk]} if url.endswith("keys") else old_transport(method,url,**kwargs)
    fresh=jwt.encode(claims,new_key,algorithm="RS256",headers={"kid":"key-2"})
    assert oidc.validate(fresh,"nonce")["sub"]==claims["sub"]
    for required in ("iss","sub","aud","exp","iat","nonce","auth_time"):
        missing=dict(claims);missing.pop(required)
        token=jwt.encode(missing,new_key,algorithm="RS256",headers={"kid":"key-2"})
        with pytest.raises((ValueError,jwt.PyJWTError)):
            oidc.validate(token,"nonce")


def test_browser_session_expiry_and_removed_member_survive_store_reopen(provider,tmp_path):
    _,_,claims=provider
    sessions=Sessions(tmp_path/"access.db");sid,owner=sessions.create()
    sessions.identity.member(sid,claims["iss"],claims["sub"],"owner",True,"operator")
    token=sessions.identity.issue(claims)
    assert sessions.identity.login(token)["expires_at"]<=time.time()+300
    sessions._exec("UPDATE workforce_logins SET expires=0")
    reopened=Sessions(tmp_path/"access.db");reopened.oidc_only=True
    assert reopened.access(sid,token) is None
    assert reopened.access(sid,owner) is None

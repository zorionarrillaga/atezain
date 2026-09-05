"""Verified subject memberships and short-lived browser sessions, stored across workers."""
import hashlib
import json
import secrets
import time


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class IdentityStore:
    def __init__(self, db):
        self.db = db
        with db.transaction("identity_schema") as execute:
            execute("CREATE TABLE IF NOT EXISTS workforce_members (workspace TEXT NOT NULL, issuer TEXT NOT NULL, "
                    "subject TEXT NOT NULL, role TEXT NOT NULL, enabled INTEGER NOT NULL, "
                    "PRIMARY KEY (workspace, issuer, subject))")
            execute("CREATE TABLE IF NOT EXISTS workforce_logins (token_hash TEXT PRIMARY KEY, issuer TEXT NOT NULL, "
                    "subject TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL, csrf TEXT NOT NULL)")
            execute("CREATE TABLE IF NOT EXISTS oauth_attempts (state_hash TEXT PRIMARY KEY, binding TEXT NOT NULL, "
                    "payload TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)")
            execute("CREATE TABLE IF NOT EXISTS identity_events (id TEXT PRIMARY KEY, at DOUBLE PRECISION NOT NULL, "
                    "actor TEXT NOT NULL, workspace TEXT NOT NULL, detail TEXT NOT NULL)")

    def member(self, workspace, issuer, subject, role, enabled, actor):
        if role not in {"owner", "reviewer", "viewer"} or not subject or len(subject) > 255:
            raise ValueError("invalid membership")
        with self.db.transaction("identity") as execute:
            execute("INSERT INTO workforce_members VALUES (?,?,?,?,?) ON CONFLICT (workspace,issuer,subject) "
                    "DO UPDATE SET role=excluded.role, enabled=excluded.enabled",
                    (workspace, issuer, subject, role, int(enabled)))
            execute("INSERT INTO identity_events VALUES (?,?,?,?,?)", (secrets.token_hex(16), time.time(), actor,
                    workspace, json.dumps({"issuer": issuer, "subject": subject, "role": role, "enabled": enabled})))
            # Removing a member also invalidates their existing logins. Re-enabling them needs a new sign-in.
            if not enabled:
                execute("DELETE FROM workforce_logins WHERE issuer=? AND subject=?", (issuer, subject))

    def members(self, workspace):
        with self.db.transaction("identity") as execute:
            return [dict(zip(("issuer", "subject", "role", "enabled"), r)) for r in execute(
                "SELECT issuer,subject,role,enabled FROM workforce_members WHERE workspace=? ORDER BY subject", (workspace,))]

    def events(self, workspace):
        with self.db.transaction("identity") as execute:
            return [{"id":r[0], "at":r[1], "actor":r[2], "detail":json.loads(r[3])} for r in execute(
                "SELECT id,at,actor,detail FROM identity_events WHERE workspace=? ORDER BY at,id", (workspace,))]

    def issue(self, claims):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = min(float(claims["exp"]), time.time() + 300)
        with self.db.transaction("identity") as execute:
            execute("DELETE FROM workforce_logins WHERE expires<=?", (time.time(),))
            execute("INSERT INTO workforce_logins VALUES (?,?,?,?,?)",
                    (digest(token), claims["iss"], claims["sub"], expires, csrf))
        return token

    def login(self, token):
        if not token or len(token) > 256:
            return None
        with self.db.transaction("identity") as execute:
            row = execute("SELECT issuer,subject,expires,csrf FROM workforce_logins WHERE token_hash=? AND expires>?",
                          (digest(token), time.time())).fetchone()
        return dict(zip(("issuer", "subject", "expires_at", "csrf"), row)) if row else None

    def access(self, workspace, token):
        login = self.login(token)
        if not login:
            return None
        with self.db.transaction("identity") as execute:
            member = execute("SELECT role FROM workforce_members WHERE workspace=? AND issuer=? AND subject=? AND enabled=1",
                             (workspace, login["issuer"], login["subject"])).fetchone()
        if not member:
            return None
        key = digest(json.dumps([login["issuer"], login["subject"]]))
        return {"id": key, "identity": "oidc:" + key, "role": member[0], "expires_at": login["expires_at"],
                "issuer": login["issuer"], "subject": login["subject"], "verified": True}

    def workspaces(self, token):
        login = self.login(token)
        if not login:
            return []
        with self.db.transaction("identity") as execute:
            return [r[0] for r in execute("SELECT m.workspace FROM workforce_members m JOIN sessions s ON s.id=m.workspace "
                "WHERE m.issuer=? AND m.subject=? AND m.enabled=1 AND s.id NOT IN (SELECT id FROM deleted_sessions)",
                (login["issuer"], login["subject"]))]

    def logout(self, token):
        with self.db.transaction("identity") as execute:
            execute("DELETE FROM workforce_logins WHERE token_hash=?", (digest(token or ""),))

    def begin(self, binding, payload):
        state = secrets.token_urlsafe(32)
        with self.db.transaction("oauth") as execute:
            execute("DELETE FROM oauth_attempts WHERE expires<=?", (time.time(),))
            if execute("SELECT COUNT(*) FROM oauth_attempts").fetchone()[0] >= 1000:
                raise OverflowError("sign-in capacity reached")
            execute("INSERT INTO oauth_attempts VALUES (?,?,?,?)", (digest(state), digest(binding), json.dumps(payload), time.time()+300))
        return state

    def consume(self, state, binding):
        if not state or not binding or max(len(state), len(binding)) > 256:
            raise ValueError("invalid sign-in state")
        with self.db.transaction("oauth") as execute:
            row = execute("SELECT binding,payload,expires FROM oauth_attempts WHERE state_hash=?", (digest(state),)).fetchone()
            if not row or row[2] <= time.time() or not secrets.compare_digest(row[0], digest(binding)):
                raise ValueError("invalid sign-in state")
            execute("DELETE FROM oauth_attempts WHERE state_hash=?", (digest(state),))
        return json.loads(row[1])

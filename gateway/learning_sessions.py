"""Persist the central session behind native Gramps JWTs in the teaching deployment."""
import base64
import contextlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


class SessionDenied(ValueError):
    pass


def group_role(groups):
    if not isinstance(groups, list):
        raise SessionDenied("Participation is not approved")
    if "curators" in groups:
        return 4
    if "students" in groups:
        return 3
    raise SessionDenied("Participation is not approved")


class Sessions:
    def __init__(self, path, issuer, client_id, client_secret, transport=None):
        self.path = Path(path)
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport or self.post
        url = urlsplit(self.issuer)
        if url.scheme != "https" or not url.netloc or url.query or url.fragment or not client_secret:
            raise ValueError("A fixed HTTPS issuer and confidential client are required")
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS authority(issuer TEXT PRIMARY KEY)")
            old = db.execute("SELECT issuer FROM authority").fetchone()
            if old and old[0] != self.issuer:
                raise ValueError("Changing issuer requires an explicit identity migration")
            db.execute("INSERT OR IGNORE INTO authority VALUES(?)", (self.issuer,))
            db.execute("""CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY, subject TEXT NOT NULL, user_id TEXT NOT NULL,
                role INTEGER NOT NULL, token TEXT NOT NULL, expires REAL NOT NULL)""")

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def post(self, endpoint, form):
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        request = Request(self.issuer + "/protocol/openid-connect/" + endpoint,
                          data=urlencode(form).encode(), headers={
                              "Authorization": "Basic " + credentials,
                              "Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(request, timeout=5) as response:
            raw = response.read(131073)
            if len(raw) > 131072:
                raise SessionDenied("Invalid identity provider response")
            return json.loads(raw)

    def create(self, subject, user_id, role, token):
        if not token.get("access_token") or not token.get("refresh_token"):
            raise SessionDenied("A renewable central session is required")
        sid = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?)", (
                sid, subject, str(user_id), role, json.dumps(token), time.time() + 8 * 3600))
        return sid

    def validate(self, sid, user_id):
        # A transaction serializes rotating refresh tokens across processes as well as threads.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sessions WHERE id = ? AND user_id = ?", (sid, str(user_id))).fetchone()
            if row is None or row["expires"] <= time.time():
                raise SessionDenied("Session expired")
            token = json.loads(row["token"])
            try:
                if token.get("expires_at", 0) < time.time() + 30:
                    refreshed = self.transport("token", {"grant_type": "refresh_token", "refresh_token": token["refresh_token"]})
                    token.update(refreshed)
                    token["expires_at"] = time.time() + int(refreshed["expires_in"])
                    db.execute("UPDATE sessions SET token = ? WHERE id = ?", (json.dumps(token), sid))
                    # Persist rotation even if the subsequent introspection is unavailable.
                    db.commit()
                    db.execute("BEGIN IMMEDIATE")
                state = self.transport("token/introspect", {"token": token["access_token"]})
                if state.get("active") is not True or state.get("sub") != row["subject"]:
                    raise SessionDenied("Central session is inactive")
                if state.get("client_id") != self.client_id or group_role(state.get("groups")) != row["role"]:
                    raise SessionDenied("Membership changed; sign in again")
            except SessionDenied:
                raise
            except Exception as exc:
                raise SessionDenied("Central session could not be verified") from exc
            return {"issuer": self.issuer, "subject": row["subject"], "user_id": row["user_id"], "role": row["role"]}


def install(app, data):
    """Attach to the pinned API only when the separate teaching mode is enabled."""
    from flask import abort, g, jsonify, redirect, request, session
    from flask_jwt_extended import create_access_token, create_refresh_token, get_jwt, get_jwt_identity, verify_jwt_in_request
    from gramps_webapi.auth import get_name, get_user_details
    from gramps_webapi.auth.oidc import create_or_update_oidc_user
    from gramps_webapi.api.resources.oidc import _get_oidc_client, _store_exchange_code, _validate_tree_id, SESSION_TREE_KEY
    from gramps_webapi.api.resources.token import get_tree_id_and_permissions

    sessions = Sessions(data / "learning-sessions.sqlite", app.config["OIDC_ISSUER"],
                        app.config["OIDC_CLIENT_ID"], app.config["OIDC_CLIENT_SECRET"])
    app.extensions["learning_sessions"] = sessions

    def mint(user_id, sid, fresh=False, refresh=False):
        name = get_name(user_id)
        details = get_user_details(name)
        if not details or details["role"] < 3:
            raise SessionDenied("Account is disabled")
        tree, permissions = get_tree_id_and_permissions(user_id=user_id, username=name)
        claims = {"permissions": list(permissions), "tree": tree, "oidc_provider": "custom", "learning_session": sid}
        result = {"access_token": create_access_token(identity=str(user_id), additional_claims=claims, fresh=fresh)}
        if refresh:
            result["refresh_token"] = create_refresh_token(identity=str(user_id), additional_claims={"learning_session": sid})
        return result

    @app.before_request
    def teaching_session():
        path = request.path.rstrip("/")
        if path == "/api/oidc/callback/custom":
            if request.method != "GET":
                abort(405)
            try:
                client, _ = _get_oidc_client("custom")
                token = client.authorize_access_token()
                claims = dict(token.get("userinfo") or {})
                if claims.get("iss") != sessions.issuer or not claims.get("sub"):
                    raise SessionDenied("Issuer mismatch")
                profile = dict(client.userinfo(token=token))
                if profile.get("sub") != claims["sub"]:
                    raise SessionDenied("Userinfo subject mismatch")
                claims.update(profile)
                if claims.get("iss") != sessions.issuer:
                    raise SessionDenied("Issuer mismatch")
                role = group_role(claims.get("groups"))
                tree = _validate_tree_id(session.pop(SESSION_TREE_KEY, None))
                user_id = create_or_update_oidc_user(claims, tree, "custom")
                sid = sessions.create(claims["sub"], user_id, role, token)
                tokens = mint(user_id, sid, fresh=True, refresh=True)
                tokens["id_token"] = token.get("id_token")
                code = _store_exchange_code(tokens)
                return redirect(app.config["BASE_URL"].rstrip("/") + "/oidc/complete#code=" + code)
            except Exception:
                # Do not echo tokens, provider response bodies or user attributes.
                return jsonify(message="Не удалось подтвердить участие. Повторите вход или обратитесь к куратору."), 401
        if path in {"/api/oidc/config", "/api/oidc/login", "/api/oidc/tokens", "/api/oidc/logout"}:
            return None
        if path.startswith("/api/oidc/") or path in {"/api/token", "/api/token/sync", "/api/token/create_owner", "/api/token/onboarding"}:
            return jsonify(message="Используйте единый вход проекта"), 403
        if not path.startswith("/api/") or (path == "/api/metadata" and not request.headers.get("Authorization")):
            return None
        try:
            import re
            locations = ["headers"]
            if request.method in {"GET", "HEAD"} and re.match(r"^/api/(media/|maps/|exporters/)", path):
                locations.append("query_string")
            verify_jwt_in_request(refresh=path == "/api/token/refresh", locations=locations)
            sid = get_jwt().get("learning_session", "")
            user_id = get_jwt_identity()
            actor = sessions.validate(sid, user_id)
            details = get_user_details(get_name(user_id))
            if not details or details["role"] != actor["role"]:
                raise SessionDenied("Account permissions changed")
            g.learning_actor = actor
            if path == "/api/learning/session":
                return jsonify(role=actor["role"], actor=actor)
            if path == "/api/token/refresh":
                if request.method != "POST":
                    abort(405)
                return jsonify(mint(user_id, sid))
        except (SessionDenied, ValueError):
            return jsonify(message="Сеанс завершён. Войдите в проект заново."), 401
        return None

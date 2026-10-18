"""Teaching integration check inside the pinned API, without external accounts."""
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="teaching-", dir="/work") as folder:
    os.environ.update(GRAMPS_RUNTIME_DATA=folder, MASTER_ORIGIN="https://gen.example.test",
                      GRAMPS_TREE_NAME="Synthetic teaching", GRAMPS_OWNER_USER="owner",
                      GRAMPS_OWNER_PASSWORD="synthetic-runtime-password", SEMARHIV_LEARNING="true",
                      OIDC_ISSUER="https://auth.example.test/realms/test", OIDC_CLIENT_ID="gramps",
                      OIDC_CLIENT_SECRET="synthetic-client-secret")
    from gateway.runtime import create
    from gramps_webapi.api.resources import oidc
    claims = dict(iss=os.environ["OIDC_ISSUER"], sub="synthetic-student", preferred_username="student", groups=["students"])

    class Provider:
        def authorize_access_token(self):
            return dict(userinfo=claims, access_token="synthetic-access", refresh_token="synthetic-refresh", expires_at=time.time() + 300)

        def userinfo(self, token):
            return claims

    # The pinned Authlib callback's verified result is substituted; token/session
    # persistence, native JWT creation, exchange and resource guards are real.
    with patch.object(oidc, "_get_oidc_client", return_value=(Provider(), {})):
        app = create()
        client = app.test_client()
        assert client.post("/api/token/", json={"username": "owner", "password": "synthetic-runtime-password"}).status_code == 403
        assert client.get("/api/oidc/config/").json["disable_local_auth"] is True
        with patch.object(Provider, "userinfo", return_value={"sub": "another-person"}):
            assert client.get("/api/oidc/callback/custom").status_code == 401
        response = client.get("/api/oidc/callback/custom")
    assert response.status_code == 302, response.get_data(as_text=True)
    code = parse_qs(urlsplit(response.location).fragment)["code"][0]
    exchange = client.post("/api/oidc/tokens/", json={"code": code})
    assert exchange.status_code == 200, exchange.get_data(as_text=True)
    assert client.post("/api/oidc/tokens/", json={"code": code}).status_code != 200
    tokens = exchange.json
    state = dict(active=True, sub=claims["sub"], client_id="gramps", groups=["students"])
    sessions = app.extensions["learning_sessions"]
    sessions.transport = lambda *args: state
    access = {"Authorization": "Bearer " + tokens["access_token"]}
    refresh = {"Authorization": "Bearer " + tokens["refresh_token"]}
    result = client.get("/api/learning/session/", headers=access)
    assert result.status_code == 200 and result.json["role"] == 3, result.get_data(as_text=True)
    assert client.post("/api/token/refresh/", headers=refresh).status_code == 200
    state["active"] = False
    assert client.get("/api/learning/session/", headers=access).status_code == 401
    assert client.post("/api/token/refresh/", headers=refresh).status_code == 401
    assert (Path(folder) / "learning-sessions.sqlite").exists()
    assert os.getuid() != 0
    print(json.dumps({"teaching_callback": "passed", "one_use_exchange": "passed", "central_revocation": "passed", "native_refresh_revocation": "passed"}))

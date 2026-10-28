# test_commercial_graph_connection.py
"""
Regression tests for the separate commercial Graph session.
Version: 0.261.033
Implemented in: 0.261.033
"""

import base64
import hashlib
import importlib.util
import logging
import os
import subprocess
import sys
import time
import types
import uuid
from pathlib import Path
from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet
from flask import Blueprint, Flask, session
from flask.sessions import SecureCookieSessionInterface


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "application" / "single_app" / "commercial_graph_connection.py"
APP = MODULE.parent
TENANT = str(uuid.UUID("11111111-1111-1111-1111-111111111111"))
OID = "22222222-2222-2222-2222-222222222222"


class SessionInterface(SecureCookieSessionInterface):
    cache = True

    def __init__(self):
        self.saved = {}

    def _get_store_id(self, sid):
        return sid

    def _retrieve_session_data(self, sid):
        return self.saved.get(sid)


class Cache:
    def __init__(self):
        self.value = "{}"

    def deserialize(self, value):
        self.value = value

    def serialize(self):
        return self.value


class Client:
    def __init__(self, cache):
        self.cache = cache
        self.on_refresh = None

    def initiate_auth_code_flow(self, **kwargs):
        verifier = "private-verifier"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = urlencode({"state": "state", "redirect_uri": kwargs["redirect_uri"],
                           "code_challenge": challenge, "code_challenge_method": "S256",
                           "nonce": hashlib.sha256(b"nonce").hexdigest()})
        return {"auth_uri": f"https://login.microsoftonline.com/authorize?{query}", "state": "state",
                "code_verifier": verifier, "nonce": "nonce"}

    def acquire_token_by_auth_code_flow(self, flow, response):
        assert flow["state"] == response["state"]
        self.cache.value = '{"refresh_token":"private-token"}'
        return {"access_token": "private-access", "id_token_claims": {
            "tid": TENANT, "oid": OID, "nonce": hashlib.sha256(b"nonce").hexdigest(),
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "aud": "33333333-3333-3333-3333-333333333333", "exp": int(time.time()) + 3600}}

    def get_accounts(self):
        return [{"home_account_id": "home", "local_account_id": OID, "realm": TENANT,
                 "username": "mail@example.com"}]

    def acquire_token_silent(self, scopes, account):
        self.cache.value = '{"refresh_token":"refreshed-private-token"}'
        if self.on_refresh:
            self.on_refresh()
        return {"access_token": "private-access"}


class Redis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    def delete(self, key):
        self.data.pop(key, None)

    def eval(self, script, count, key, previous, value, expires):
        if self.data.get(key) != previous:
            return 0
        self.data[key] = value
        return 1


@pytest.fixture
def context(monkeypatch, tmp_path):
    store = tmp_path / "commercial-graph-regression.sqlite3"
    monkeypatch.setitem(sys.modules, "functions_appinsights", types.SimpleNamespace(log_event=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "swagger_wrapper", types.SimpleNamespace(
        swagger_route=lambda **kwargs: lambda function: function, get_auth_security=lambda: []))
    spec = importlib.util.spec_from_file_location("commercial_graph_test_module", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.test_store = store
    for key, value in {
        "COMMERCIAL_GRAPH_CLIENT_ID": "33333333-3333-3333-3333-333333333333",
        "COMMERCIAL_GRAPH_CLIENT_SECRET": "client-secret",
        "COMMERCIAL_GRAPH_TENANT_ID": TENANT,
        "COMMERCIAL_GRAPH_REDIRECT_URI": "https://app.example.com/auth/commercial-graph/callback",
        "COMMERCIAL_GRAPH_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "CLIENT_ID": "44444444-4444-4444-4444-444444444444",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(module, "_new_cache", Cache)
    clients = []

    def client_factory(values, tenant, cache):
        client = Client(cache)
        clients.append(client)
        return client

    monkeypatch.setattr(module, "_client", client_factory)
    app = Flask(__name__)
    app.secret_key = "test-only"
    app.config.update(SESSION_TYPE="filesystem", SESSION_FILE_DIR=str(store.parent),
                      COMMERCIAL_GRAPH_SESSION_ACTIVE=lambda data: True)
    app.session_interface = SessionInterface()
    module.real_backend = module._backend
    monkeypatch.setattr(module, "_backend", lambda: ("filesystem", str(store)))
    bp = Blueprint("commercial", __name__)
    module.register_commercial_graph_routes(bp)
    app.register_blueprint(bp)
    yield app, module, clients


def _session(app):
    session.sid = "government-session"
    session["user"] = {"tid": "government-tenant", "oid": "government-user", "roles": ["User"]}
    app.session_interface.saved[session.sid] = dict(session)


def _connect(app):
    with app.test_request_context("/api/commercial-graph/connect", method="POST",
                                  headers={"Origin": "https://app.example.com"}):
        _session(app)
        response = app.view_functions["commercial.commercial_graph_connect"]()
        assert response.get_json()["auth_url"].startswith("https://")
        handle = session["commercial_graph_handle"]
        app.session_interface.saved[session.sid] = dict(session)
        return handle


def _callback(app, handle, state="state"):
    with app.test_request_context(f"/auth/commercial-graph/callback?state={state}&code=code"):
        _session(app)
        session["commercial_graph_handle"] = handle
        app.session_interface.saved[session.sid] = dict(session)
        return app.view_functions["commercial.commercial_graph_callback"]()


def _token(app, module, handle):
    with app.test_request_context("/api/chat"):
        _session(app)
        session["commercial_graph_handle"] = handle
        app.session_interface.saved[session.sid] = dict(session)
        return module.commercial_graph_token(["Mail.Read"])


def test_flow_refresh_is_durable_and_encrypted(context):
    app, module, _ = context
    handle = _connect(app)
    response = _callback(app, handle)
    assert response.location == "/profile?tab=settings#commercial-mail"
    result = _token(app, module, handle)
    assert result == {"access_token": "private-access"}
    with app.test_request_context():
        stored = module._read(handle)
        assert b"private-token" not in stored
        record = module._unseal(stored, module._configuration()[2])
    assert "refreshed-private-token" in record["cache"]
    assert b"private-verifier" not in module.test_store.read_bytes()


def test_bad_state_does_not_consume_flow(context):
    app, module, _ = context
    handle = _connect(app)
    _callback(app, handle, "wrong")
    result = _token(app, module, handle)
    assert "error" in result
    _callback(app, handle)
    assert _token(app, module, handle) == {"access_token": "private-access"}


def test_disconnect_during_refresh_cannot_resurrect_token(context):
    app, module, clients = context
    handle = _connect(app)
    _callback(app, handle)
    with app.test_request_context("/api/chat"):
        _session(app)
        session["commercial_graph_handle"] = handle
        app.session_interface.saved[session.sid] = dict(session)
        original_factory = module._client

        def factory(*args):
            client = original_factory(*args)
            client.on_refresh = lambda: module._delete(handle)
            return client

        module._client = factory
        result = module.commercial_graph_token(["Mail.Read"])
        stored = module._read(handle)
    assert "error" in result
    assert stored is None


def test_csrf_logout_and_generation_invalidation(context):
    app, module, _ = context
    handle = _connect(app)
    _callback(app, handle)
    with app.test_request_context("/api/commercial-graph/disconnect", method="POST",
                                  headers={"Origin": "https://attacker.example"}):
        _session(app)
        session["commercial_graph_handle"] = handle
        response, status = app.view_functions["commercial.commercial_graph_disconnect"]()
        assert status == 403
    with app.test_request_context("/logout"):
        _session(app)
        session["commercial_graph_handle"] = handle
        module.capture_commercial_graph_session()
        session.clear()
        response = module.revoke_cleared_commercial_graph_session(app.response_class())
        assert response.status_code == 200
    result = _token(app, module, handle)
    assert "error" in result


def test_redis_refresh_and_concurrent_disconnect(context, monkeypatch):
    app, module, _ = context
    redis = Redis()
    monkeypatch.setattr(module, "_backend", lambda: ("redis", redis))
    handle = _connect(app)
    _callback(app, handle)
    result = _token(app, module, handle)
    assert result == {"access_token": "private-access"}
    with app.test_request_context("/api/chat"):
        _session(app)
        session["commercial_graph_handle"] = handle
        app.session_interface.saved[session.sid] = dict(session)
        original_factory = module._client

        def factory(*args):
            client = original_factory(*args)
            client.on_refresh = lambda: module._set_generation(session.sid, b"revoked")
            return client

        monkeypatch.setattr(module, "_client", factory)
        result = module.commercial_graph_token(["Mail.ReadWrite"])
    assert "error" in result
    assert all(b"private-token" not in value for value in redis.data.values())


def test_configuration_and_session_owner_fail_closed(context, monkeypatch):
    app, module, _ = context
    handle = _connect(app)
    _callback(app, handle)
    with app.test_request_context("/api/chat"):
        _session(app)
        session["commercial_graph_handle"] = handle
        app.session_interface.saved[session.sid] = dict(session)
        app.session_interface.saved[session.sid]["user"] = {"tid": "other", "oid": "other"}
        result = module.commercial_graph_token(["Mail.Read"])
        assert "error" in result
        monkeypatch.setenv("COMMERCIAL_GRAPH_ENCRYPTION_KEY", "")
        assert not module.commercial_graph_configured()


def test_missing_connection_includes_actionable_profile_url(context):
    app, module, _ = context
    with app.test_request_context("/api/chat"):
        _session(app)
        result = module.commercial_graph_token(["Mail.Read"])
    assert result["connect_url"] == "/profile?tab=settings#commercial-mail"
    assert "/profile?tab=settings#commercial-mail" in result["message"]


def test_real_msal_flow_includes_state_nonce_and_pkce(context):
    _, module, _ = context
    from msal.application import _ClientWithCcsRoutingInfo

    client = _ClientWithCcsRoutingInfo(
        {"authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"},
        "33333333-3333-3333-3333-333333333333",
    )
    redirect_uri = "https://app.example.com/auth/commercial-graph/callback"
    flow = client.initiate_auth_code_flow(
        redirect_uri=redirect_uri, scope=["Mail.Read", "Mail.ReadWrite"]
    )
    module._validate_flow(flow, redirect_uri)
    flow["code_verifier"] = "tampered"
    with pytest.raises(module.ConnectionError):
        module._validate_flow(flow, redirect_uri)


def test_status_and_disconnect_contract(context):
    app, module, _ = context
    handle = _connect(app)
    _callback(app, handle)
    with app.test_request_context("/api/commercial-graph/status"):
        _session(app)
        session["commercial_graph_handle"] = handle
        app.session_interface.saved[session.sid] = dict(session)
        payload = app.view_functions["commercial.commercial_graph_status"]().get_json()
        assert payload == {"configured": True, "connected": True,
                           "account": "mail@example.com", "message": "Commercial mail connected."}
    with app.test_request_context("/api/commercial-graph/disconnect", method="POST",
                                  headers={"Origin": "https://app.example.com"}):
        _session(app)
        session["commercial_graph_handle"] = handle
        app.session_interface.saved[session.sid] = dict(session)
        response = app.view_functions["commercial.commercial_graph_disconnect"]()
        assert response.get_json() == {"success": True}
    assert "error" in _token(app, module, handle)


def test_connection_error_response_and_callback_failure_are_visible(context, monkeypatch):
    app, module, _ = context
    with app.test_request_context("/api/commercial-graph/connect", method="POST",
                                  headers={"Origin": "https://app.example.com"}):
        _session(app)
        monkeypatch.setattr(module, "_client", lambda *args: (_ for _ in ()).throw(
            module.ConnectionError("The connection could not be started.")))
        response, status = app.view_functions["commercial.commercial_graph_connect"]()
        assert status == 503
        assert response.get_json()["message"] == "The connection could not be started."
    with app.test_request_context("/auth/commercial-graph/callback?state=invalid"):
        _session(app)
        response = app.view_functions["commercial.commercial_graph_callback"]()
        assert response.location == "/profile?tab=settings&commercial_mail_error=1#commercial-mail"


def test_status_storage_failure_is_not_reported_as_success(context, monkeypatch):
    app, module, _ = context
    with app.test_request_context("/api/commercial-graph/status"):
        _session(app)
        monkeypatch.setattr(module, "_active_record", lambda: (_ for _ in ()).throw(
            OSError("private storage detail")))
        response, status = app.view_functions["commercial.commercial_graph_status"]()
        assert status == 503
        assert "private storage detail" not in response.get_json()["message"]


def test_invalid_nonce_is_rejected(context, monkeypatch):
    app, module, _ = context
    handle = _connect(app)
    original = Client.acquire_token_by_auth_code_flow

    def bad_nonce(client, flow, response):
        result = original(client, flow, response)
        result["id_token_claims"]["nonce"] = "wrong"
        return result

    monkeypatch.setattr(Client, "acquire_token_by_auth_code_flow", bad_nonce)
    _callback(app, handle)
    result = _token(app, module, handle)
    assert "error" in result


def test_cache_debug_logs_never_include_code_or_verifier(context, caplog):
    _, module, _ = context
    cache = module._CommercialTokenCache()
    event = {"client_id": "client", "data": {"code": "private-code",
             "code_verifier": "private-verifier"}, "response": {}}
    with caplog.at_level(logging.DEBUG):
        cache.add(event)
    assert "private-code" not in caplog.text
    assert "private-verifier" not in caplog.text


def test_selected_filesystem_backend_and_missing_session_fail_closed(context, monkeypatch, tmp_path):
    app, module, _ = context
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    app.config["SESSION_FILE_DIR"] = str(session_dir)
    with app.test_request_context("/api/chat"):
        _session(app)
        monkeypatch.setattr(module, "_backend", module.real_backend)
        kind, path = module._backend()
        assert kind == "filesystem"
        assert path.endswith("commercial_graph.sqlite3")
        assert Path(path).parent.parent == Path(app.config["SESSION_FILE_DIR"]).parent
        assert Path(path).parent != Path(app.config["SESSION_FILE_DIR"])


def test_real_filesystem_session_and_cache_cleanup(context, monkeypatch, tmp_path):
    flask_session = pytest.importorskip("flask_session")
    app, module, _ = context
    session_dir = tmp_path / "flask-session"
    session_dir.mkdir()
    app.config.update(SESSION_FILE_DIR=str(session_dir), SESSION_TYPE="filesystem")
    flask_session.Session(app)
    monkeypatch.setattr(module, "_backend", module.real_backend)
    app.add_url_rule("/test-commercial-token", "test_commercial_token",
                     lambda: module.jsonify(module.commercial_graph_token(["Mail.Read"])))
    client = app.test_client()
    with client.session_transaction() as saved:
        saved["user"] = {"tid": "government-tenant", "oid": "government-user", "roles": ["User"]}
    response = client.post("/api/commercial-graph/connect", headers={"Origin": "https://app.example.com"})
    assert response.status_code == 200
    response = client.get("/auth/commercial-graph/callback?state=state&code=test")
    assert response.location == "/profile?tab=settings#commercial-mail"
    response = client.get("/test-commercial-token")
    assert response.get_json() == {"access_token": "private-access"}
    store = Path(str(session_dir) + ".commercial-graph") / "commercial_graph.sqlite3"
    assert store.is_file()
    app.session_interface.cache.clear()
    assert store.is_file()


def test_missing_or_unavailable_server_session_requires_reconnect(context, monkeypatch):
    app, module, _ = context
    handle = _connect(app)
    _callback(app, handle)
    with app.test_request_context("/api/chat"):
        _session(app)
        session["commercial_graph_handle"] = handle
        app.session_interface.saved.clear()
        result = module.commercial_graph_token(["Mail.Read"])
        assert "/profile?tab=settings#commercial-mail" in result["message"]

        def unavailable(sid):
            raise RuntimeError("Redis unavailable")

        monkeypatch.setattr(app.session_interface, "_retrieve_session_data", unavailable)
        result = module.commercial_graph_token(["Mail.Read"])
        assert "cannot be recovered" in result["message"]
        assert "Redis unavailable" not in result["message"]


def test_fallback_does_not_migrate_existing_redis_connection(context, monkeypatch):
    app, module, _ = context
    redis = Redis()
    monkeypatch.setattr(module, "_backend", lambda: ("redis", redis))
    handle = _connect(app)
    _callback(app, handle)
    monkeypatch.setattr(module, "_backend", lambda: ("filesystem", str(module.test_store)))
    result = _token(app, module, handle)
    assert "error" in result
    assert result["connect_url"] == "/profile?tab=settings#commercial-mail"


@pytest.mark.parametrize("first_module", ["commercial_graph_connection", "functions_appinsights"])
@pytest.mark.parametrize("optimized", [False, True])
def test_disabled_feature_cold_import_has_no_cloud_bootstrap(first_module, optimized):
    probe = """
import importlib, os, socket, sys
import msal
import azure.monitor.opentelemetry

def fail(*args, **kwargs):
    raise RuntimeError("Unexpected client initialization or network access")

for name in tuple(os.environ):
    if name.startswith("COMMERCIAL_GRAPH_"):
        os.environ.pop(name)
socket.socket.connect = fail
msal.ConfidentialClientApplication = fail
azure.monitor.opentelemetry.configure_azure_monitor = fail
importlib.import_module(sys.argv[1])
module = importlib.import_module("commercial_graph_connection")
importlib.import_module("functions_appinsights")
if module.commercial_graph_configured():
    raise RuntimeError("Feature enabled without configuration")
if "config" in sys.modules or "functions_authentication" in sys.modules or "swagger_wrapper" in sys.modules:
    raise RuntimeError("Commercial auth imported application bootstrap")
print("cold-import-ok")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(APP), env.get("PYTHONPATH", "")])
    command = [sys.executable, "-B"]
    if optimized:
        command.append("-O")
    result = subprocess.run(command + ["-c", probe, first_module], env=env, capture_output=True,
                            text=True, timeout=180, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cold-import-ok" in result.stdout

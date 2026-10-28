# commercial_graph_connection.py
"""Separate, session-bound authentication for a commercial Graph account."""

import base64
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import msal
import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, g, jsonify, redirect, request, session

from functions_appinsights import log_event


_HANDLE = "commercial_graph_handle"
_CONNECT_URL = "/profile?tab=settings#commercial-mail"
_RETURN_URL = _CONNECT_URL
_SCOPES = ("Mail.Read", "Mail.ReadWrite")
_FLOW_LIFETIME = 600


class ConnectionError(Exception):
    """A safe, user-facing connection failure."""


class _BoundedHttpSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (5, 15))
        return super().request(method, url, **kwargs)


class _CommercialTokenCache(msal.SerializableTokenCache):
    def add(self, event, **kwargs):
        # MSAL 1.33 logs cache events at DEBUG without masking authorization codes or PKCE verifiers.
        event = dict(event)
        event["data"] = {key: value for key, value in event.get("data", {}).items()
                         if key not in {"code", "code_verifier"}}
        return super().add(event, **kwargs)


def _new_cache():
    return _CommercialTokenCache()


def _configuration():
    names = (
        "COMMERCIAL_GRAPH_CLIENT_ID",
        "COMMERCIAL_GRAPH_CLIENT_SECRET",
        "COMMERCIAL_GRAPH_TENANT_ID",
        "COMMERCIAL_GRAPH_REDIRECT_URI",
        "COMMERCIAL_GRAPH_ENCRYPTION_KEY",
    )
    values = {name: os.environ.get(name, "").strip() for name in names}
    try:
        uuid.UUID(values[names[0]])
        if os.environ.get("CLIENT_ID", "").strip().lower() == values[names[0]].lower():
            raise ValueError("Commercial client must be separate")
        tenant = str(uuid.UUID(values[names[2]]))
        uri = urlsplit(values[names[3]])
        if (uri.scheme != "https" or not uri.hostname or uri.username or uri.password
                or uri.path != "/auth/commercial-graph/callback" or uri.query or uri.fragment):
            raise ValueError("Invalid redirect")
        key = values[names[4]].encode("ascii")
        Fernet(key)
        if not values[names[1]]:
            raise ValueError("Missing secret")
    except (ValueError, TypeError, UnicodeError):
        raise ConnectionError("Commercial mail is not configured correctly. Contact your administrator.")
    return values, tenant, key


def commercial_graph_configured():
    try:
        _configuration()
        return True
    except ConnectionError:
        return False


def _owner(data):
    user = data.get("user")
    if not isinstance(user, dict):
        return None
    tenant = user.get("tid")
    identity = user.get("oid") or user.get("sub")
    if not isinstance(tenant, str) or not isinstance(identity, str) or not tenant or not identity:
        return None
    return f"{tenant}:{identity}"


def _backend():
    interface = current_app.session_interface
    backend_type = current_app.config.get("SESSION_TYPE")
    if backend_type == "redis" and hasattr(interface, "client"):
        return "redis", interface.client
    if backend_type == "filesystem" and hasattr(interface, "cache"):
        directory = current_app.config.get("SESSION_FILE_DIR")
        if directory and os.path.isdir(directory):
            store_directory = os.path.abspath(directory) + ".commercial-graph"
            os.makedirs(store_directory, mode=0o700, exist_ok=True)
            return "filesystem", os.path.join(store_directory, "commercial_graph.sqlite3")
    raise ConnectionError("Commercial mail session storage is unavailable.")


def _key(handle):
    return f"simplechat:commercial-graph:{hashlib.sha256(handle.encode('ascii')).hexdigest()}"


@contextmanager
def _sqlite(path):
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS commercial_graph "
                "(id TEXT PRIMARY KEY, payload BLOB NOT NULL, expires INTEGER NOT NULL)"
            )
            yield conn
    finally:
        conn.close()


def _read(handle):
    kind, backend = _backend()
    key = _key(handle)
    if kind == "redis":
        return backend.get(key)
    with _sqlite(backend) as conn:
        conn.execute("DELETE FROM commercial_graph WHERE expires <= ?", (int(time.time()),))
        row = conn.execute("SELECT payload FROM commercial_graph WHERE id = ?", (key,)).fetchone()
        return row[0] if row else None


def _create(handle, value, expires):
    kind, backend = _backend()
    key = _key(handle)
    if kind == "redis":
        if not backend.set(key, value, ex=max(1, expires - int(time.time())), nx=True):
            raise ConnectionError("Unable to start the commercial mail connection.")
    else:
        with _sqlite(backend) as conn:
            conn.execute("DELETE FROM commercial_graph WHERE expires <= ?", (int(time.time()),))
            conn.execute("INSERT INTO commercial_graph VALUES (?, ?, ?)", (key, value, expires))


def _replace(handle, previous, value, expires):
    """Replace only the exact existing record, never recreating a revoked handle."""
    kind, backend = _backend()
    key = _key(handle)
    if kind == "redis":
        return bool(backend.eval(
            "if redis.call('GET',KEYS[1]) ~= ARGV[1] then return 0 end "
            "redis.call('SET',KEYS[1],ARGV[2],'EX',ARGV[3]); return 1",
            1, key, previous, value, max(1, expires - int(time.time())),
        ))
    with _sqlite(backend) as conn:
        result = conn.execute(
            "UPDATE commercial_graph SET payload = ?, expires = ? "
            "WHERE id = ? AND payload = ? AND expires > ?",
            (value, expires, key, previous, int(time.time())),
        )
        return result.rowcount == 1


def _delete(handle):
    if not isinstance(handle, str) or len(handle) > 128:
        return
    kind, backend = _backend()
    key = _key(handle)
    if kind == "redis":
        backend.delete(key)
    else:
        with _sqlite(backend) as conn:
            conn.execute("DELETE FROM commercial_graph WHERE id = ?", (key,))


def _session_revoked(sid):
    return _read(f"generation:{sid}") == b"revoked"


def _set_generation(sid, value):
    kind, backend = _backend()
    key = _key(f"generation:{sid}")
    lifetime = int(current_app.permanent_session_lifetime.total_seconds()) + 86400
    if kind == "redis":
        backend.set(key, value, ex=lifetime)
    else:
        with _sqlite(backend) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO commercial_graph VALUES (?, ?, ?)",
                (key, value, int(time.time()) + lifetime),
            )


def _generation(sid):
    value = _read(f"generation:{sid}")
    if value is None:
        candidate = secrets.token_hex(32).encode("ascii")
        try:
            _create(f"generation:{sid}", candidate,
                    int(time.time() + current_app.permanent_session_lifetime.total_seconds() + 86400))
        except (ConnectionError, sqlite3.IntegrityError):
            pass
        value = _read(f"generation:{sid}")
    if not value or value == b"revoked":
        raise ConnectionError("Your session was signed out. Sign in again to connect commercial mail.")
    return value.decode("ascii")


def _seal(record, key):
    return Fernet(key).encrypt(json.dumps(record, separators=(",", ":")).encode("utf-8"))


def _unseal(payload, key):
    try:
        record = json.loads(Fernet(key).decrypt(payload))
        if not isinstance(record, dict):
            raise ValueError("Invalid record")
        return record
    except (InvalidToken, ValueError, TypeError):
        raise ConnectionError("Commercial mail connection cannot be read. Please reconnect.")


def _saved_session():
    sid = getattr(session, "sid", None)
    interface = current_app.session_interface
    if not sid or not hasattr(interface, "_retrieve_session_data"):
        raise ConnectionError("Commercial mail requires a server-side session.")
    try:
        return interface._retrieve_session_data(interface._get_store_id(sid)) or {}
    except Exception as exc:
        log_event(
            "[COMMERCIAL_GRAPH] Unable to read the current session.",
            level=logging.WARNING,
            extra={"exception_type": type(exc).__name__},
        )
        raise ConnectionError(f"Your session cannot be recovered. Sign in and reconnect at {_CONNECT_URL}.") from None


def _active_record():
    values, tenant, key = _configuration()
    handle = session.get(_HANDLE)
    owner = _owner(session)
    if not isinstance(handle, str) or not owner:
        raise ConnectionError(f"Connect your commercial mail account at {_CONNECT_URL}.")
    if _session_revoked(session.sid):
        raise ConnectionError("Your session was signed out. Sign in again to connect commercial mail.")
    saved = _saved_session()
    if saved.get(_HANDLE) != handle or _owner(saved) != owner:
        raise ConnectionError(f"Your session or connection has expired. Reconnect at {_CONNECT_URL}.")
    if not current_app.config["COMMERCIAL_GRAPH_SESSION_ACTIVE"](saved):
        raise ConnectionError(f"Your session has expired. Sign in and reconnect at {_CONNECT_URL}.")
    payload = _read(handle)
    if not payload:
        raise ConnectionError(f"Connect your commercial mail account at {_CONNECT_URL}.")
    record = _unseal(payload, key)
    if (record.get("owner") != owner or record.get("sid") != session.sid
            or record.get("tenant") != tenant or record.get("expires", 0) <= time.time()
            or _read(f"generation:{session.sid}") != record.get("generation", "").encode("ascii")):
        raise ConnectionError(f"Your connection has expired. Reconnect at {_CONNECT_URL}.")
    return handle, payload, record, values, tenant, key


def _client(values, tenant, cache):
    return msal.ConfidentialClientApplication(
        values["COMMERCIAL_GRAPH_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=values["COMMERCIAL_GRAPH_CLIENT_SECRET"],
        token_cache=cache,
        http_client=_BoundedHttpSession(),
    )


def _validate_flow(flow, redirect_uri):
    if not isinstance(flow, dict):
        raise ConnectionError("Unable to start the commercial mail connection.")
    verifier, nonce, state = (flow.get("code_verifier"), flow.get("nonce"), flow.get("state"))
    if not all(isinstance(value, str) and value for value in (verifier, nonce, state)):
        raise ConnectionError("Unable to start a secure commercial mail connection.")
    uri = urlsplit(flow.get("auth_uri", ""))
    params = parse_qs(uri.query)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    expected = {
        "state": state,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "nonce": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
    }
    if (uri.scheme != "https" or uri.hostname != "login.microsoftonline.com"
            or uri.username or uri.password or uri.port not in (None, 443)
            or any(params.get(name) != [value] for name, value in expected.items())):
        raise ConnectionError("Unable to start a secure commercial mail connection.")


def _safe_failure(exc):
    if isinstance(exc, ConnectionError):
        return str(exc)
    log_event(
        "[COMMERCIAL_GRAPH] Connection operation failed.",
        level=logging.WARNING,
        extra={"exception_type": type(exc).__name__},
    )
    return "Commercial mail is temporarily unavailable. Please try again."


def commercial_graph_token(scopes):
    """Return a commercial Graph token, or a safe error with a connection URL."""
    if not isinstance(scopes, (list, tuple)) or not scopes or any(scope not in _SCOPES for scope in scopes):
        message = "Unsupported commercial mail permission."
        return {"error": message, "message": message, "connect_url": _CONNECT_URL}
    try:
        handle, payload, record, values, tenant, key = _active_record()
        if not record.get("account_id") or not record.get("cache"):
            raise ConnectionError(f"Connect your commercial mail account at {_CONNECT_URL}.")
        cache = _new_cache()
        cache.deserialize(record["cache"])
        client = _client(values, tenant, cache)
        accounts = [account for account in client.get_accounts()
                    if account.get("home_account_id") == record["account_id"]
                    and account.get("realm", "").lower() == tenant]
        if len(accounts) != 1:
            raise ConnectionError(f"Your commercial account is unavailable. Reconnect at {_CONNECT_URL}.")
        result = client.acquire_token_silent(list(scopes), account=accounts[0])
        if not isinstance(result, dict) or not result.get("access_token"):
            raise ConnectionError(f"Commercial mail needs consent or sign-in. Reconnect at {_CONNECT_URL}.")
        record["cache"] = cache.serialize()
        if not _replace(handle, payload, _seal(record, key), record["expires"]):
            raise ConnectionError(f"Your connection changed. Reconnect at {_CONNECT_URL}.")
        saved = _saved_session()
        if (saved.get(_HANDLE) != handle or _owner(saved) != record["owner"]
                or not current_app.config["COMMERCIAL_GRAPH_SESSION_ACTIVE"](saved)
                or _read(f"generation:{session.sid}") != record["generation"].encode("ascii")
                or not _read(handle)):
            raise ConnectionError(f"Your session or connection has expired. Reconnect at {_CONNECT_URL}.")
        return {"access_token": result["access_token"]}
    except Exception as exc:
        message = _safe_failure(exc)
        return {"error": message, "message": message, "connect_url": _CONNECT_URL}


def _same_origin():
    values, _, _ = _configuration()
    expected = urlsplit(values["COMMERCIAL_GRAPH_REDIRECT_URI"])
    fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
    if fetch_site == "cross-site":
        return False
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        return fetch_site == "same-origin"
    actual = urlsplit(origin)
    return (actual.scheme, actual.hostname, actual.port or 443) == (
        expected.scheme, expected.hostname, expected.port or 443
    )


def register_commercial_graph_routes(bp):
    # Route registration occurs after authentication bootstrap; the plugin imports this module earlier.
    from swagger_wrapper import get_auth_security, swagger_route

    @bp.route("/api/commercial-graph/status", methods=["GET"])
    @swagger_route(security=get_auth_security())
    def commercial_graph_status():
        if not commercial_graph_configured():
            return jsonify(configured=False, connected=False, account=None,
                           message="Commercial mail is not configured. Contact your administrator.")
        try:
            _, _, record, _, _, _ = _active_record()
            connected = bool(record.get("account_id") and record.get("cache"))
            return jsonify(configured=True, connected=connected,
                           account=record.get("account_name") if connected else None,
                           message="Commercial mail connected." if connected else
                           record.get("error", "Connect your commercial mail account."))
        except ConnectionError as exc:
            return jsonify(configured=True, connected=False, account=None, message=_safe_failure(exc))
        except Exception as exc:
            return jsonify(message=_safe_failure(exc)), 503

    @bp.route("/api/commercial-graph/connect", methods=["POST"])
    @swagger_route(security=get_auth_security())
    def commercial_graph_connect():
        try:
            if not _same_origin():
                return jsonify(message="Connection requests must originate from SimpleChat."), 403
            values, tenant, key = _configuration()
            owner = _owner(session)
            sid = getattr(session, "sid", None)
            if not owner or not sid:
                raise ConnectionError("Sign in to SimpleChat before connecting commercial mail.")
            if _session_revoked(sid) or _owner(_saved_session()) != owner:
                raise ConnectionError("Your session was signed out. Sign in again to connect commercial mail.")
            generation = _generation(sid)
            cache = _new_cache()
            client = _client(values, tenant, cache)
            flow = client.initiate_auth_code_flow(
                scopes=list(_SCOPES), redirect_uri=values["COMMERCIAL_GRAPH_REDIRECT_URI"],
                prompt="select_account",
            )
            _validate_flow(flow, values["COMMERCIAL_GRAPH_REDIRECT_URI"])
            old = session.get(_HANDLE)
            if old:
                _delete(old)
            handle = secrets.token_urlsafe(32)
            now = int(time.time())
            lifetime = int(current_app.permanent_session_lifetime.total_seconds())
            record = {"owner": owner, "sid": sid, "tenant": tenant, "flow": flow,
                      "generation": generation, "flow_expires": now + _FLOW_LIFETIME,
                      "expires": now + lifetime}
            _create(handle, _seal(record, key), record["expires"])
            if (_read(f"generation:{sid}") != generation.encode("ascii")
                    or _owner(_saved_session()) != owner):
                _delete(handle)
                raise ConnectionError("Your session was signed out. Sign in again to connect commercial mail.")
            session[_HANDLE] = handle
            return jsonify(auth_url=flow["auth_uri"])
        except Exception as exc:
            return jsonify(message=_safe_failure(exc)), 503

    @bp.route("/auth/commercial-graph/callback", methods=["GET"])
    @swagger_route(security=get_auth_security())
    def commercial_graph_callback():
        consumed = None
        succeeded = False
        try:
            handle, payload, record, values, tenant, key = _active_record()
            flow = record.pop("flow", None)
            if not flow or record.get("flow_expires", 0) <= time.time():
                raise ConnectionError("Commercial mail sign-in expired. Please reconnect.")
            state = request.args.get("state", "")
            if (len(request.args.getlist("state")) != 1 or not isinstance(flow.get("state"), str)
                    or not secrets.compare_digest(state, flow["state"])):
                raise ConnectionError("Commercial mail sign-in could not be verified.")
            # Consume the flow before redeeming the code so concurrent callbacks cannot replay it.
            consumed = _seal(record, key)
            if not _replace(handle, payload, consumed, record["expires"]):
                raise ConnectionError("Commercial mail sign-in was already used.")
            cache = _new_cache()
            client = _client(values, tenant, cache)
            result = client.acquire_token_by_auth_code_flow(flow, request.args.to_dict(flat=True))
            claims = result.get("id_token_claims") if isinstance(result, dict) else None
            expected_nonce = hashlib.sha256(flow["nonce"].encode("ascii")).hexdigest()
            if (not isinstance(result, dict) or not result.get("access_token") or not isinstance(claims, dict)
                    or str(claims.get("tid", "")).lower() != tenant
                    or not claims.get("oid")
                    or claims.get("iss") != f"https://login.microsoftonline.com/{tenant}/v2.0"
                    or claims.get("aud") != values["COMMERCIAL_GRAPH_CLIENT_ID"]
                    or not isinstance(claims.get("exp"), (int, float))
                    or claims["exp"] <= time.time()
                    or not isinstance(claims.get("nbf", 0), (int, float))
                    or claims.get("nbf", 0) > time.time() + 120
                    or not isinstance(claims.get("nonce"), str)
                    or not secrets.compare_digest(claims["nonce"], expected_nonce)):
                raise ConnectionError("The commercial account could not be verified.")
            accounts = client.get_accounts()
            matches = [account for account in accounts
                       if account.get("local_account_id", "").lower() == str(claims["oid"]).lower()
                       and account.get("realm", "").lower() == tenant]
            if len(matches) != 1 or not matches[0].get("home_account_id"):
                raise ConnectionError("The commercial account could not be verified.")
            account_name = matches[0].get("username") or claims.get("preferred_username")
            if not isinstance(account_name, str):
                account_name = None
            record.update(cache=cache.serialize(), account_id=matches[0]["home_account_id"],
                          account_name=account_name)
            if not _replace(handle, consumed, _seal(record, key), record["expires"]):
                raise ConnectionError("Commercial mail connection was disconnected.")
            succeeded = True
        except Exception as exc:
            _safe_failure(exc)
            if consumed is not None:
                try:
                    record["error"] = "Commercial mail sign-in failed. Check consent and try connecting again."
                    _replace(handle, consumed, _seal(record, key), record["expires"])
                except Exception as save_error:
                    _safe_failure(save_error)
        destination = _RETURN_URL if succeeded else "/profile?tab=settings&commercial_mail_error=1#commercial-mail"
        response = redirect(destination)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @bp.route("/api/commercial-graph/disconnect", methods=["POST"])
    @swagger_route(security=get_auth_security())
    def commercial_graph_disconnect():
        try:
            if not _same_origin():
                return jsonify(message="Disconnect requests must originate from SimpleChat."), 403
            sid = getattr(session, "sid", None)
            if not sid or _session_revoked(sid):
                raise ConnectionError("Your session has expired. Sign in again.")
            generation = _generation(sid).encode("ascii")
            expires = int(time.time() + current_app.permanent_session_lifetime.total_seconds() + 86400)
            if not _replace(f"generation:{sid}", generation, secrets.token_hex(32).encode("ascii"), expires):
                raise ConnectionError("Your session changed. Please retry.")
            _delete(session.get(_HANDLE))
            session.pop(_HANDLE, None)
            return jsonify(success=True)
        except Exception as exc:
            return jsonify(message=_safe_failure(exc)), 503


def capture_commercial_graph_session():
    g.commercial_graph_original = (session.get(_HANDLE), _owner(session), getattr(session, "sid", None))


def revoke_cleared_commercial_graph_session(response):
    previous = getattr(g, "commercial_graph_original", None)
    if previous and previous[0] and session.get(_HANDLE) != previous[0]:
        try:
            _delete(previous[0])
        except Exception as exc:
            _safe_failure(exc)
            response = jsonify(error="Unable to revoke commercial mail. Please retry.")
            response.status_code = 503
            return response
    if (previous and previous[1] and previous[2] and _owner(session) != previous[1]
            and (previous[0] or commercial_graph_configured())):
        try:
            _set_generation(previous[2], b"revoked")
            if previous[0]:
                _delete(previous[0])
        except Exception as exc:
            _safe_failure(exc)
            response = jsonify(error="Unable to revoke commercial mail. Please retry.")
            response.status_code = 503
            return response
    return response

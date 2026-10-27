# admin_settings_diagnostics.py
"""Temporary, settings-independent diagnostics for the admin settings GET."""

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps


_request_id = ContextVar("admin_settings_diagnostic_request_id", default=None)
_logger = logging.getLogger("simplechat.admin_settings_diagnostics")
_logger.setLevel(logging.INFO)
_logger.propagate = False
if not any(getattr(handler, "_admin_settings_diagnostic", False) for handler in _logger.handlers):
    _handler = logging.StreamHandler()
    _handler._admin_settings_diagnostic = True
    _logger.addHandler(_handler)


def _emit(stage, event, duration_ms=None, error_type=None, status_code=None, force=False):
    request_id = _request_id.get()
    if request_id is None and not force:
        return
    message = "[ADMIN_SETTINGS_DIAGNOSTIC]"
    if request_id is not None:
        message += f" request_id={request_id}"
    message += f" stage={stage} event={event}"
    if duration_ms is not None:
        message += f" duration_ms={duration_ms}"
    if error_type is not None:
        message += f" error_type={error_type}"
    if status_code is not None:
        message += f" status_code={status_code}"
    _logger.info(message)


@contextmanager
def admin_settings_stage(stage):
    if _request_id.get() is None:
        yield
        return
    started = time.perf_counter()
    _emit(stage, "started")
    try:
        yield
    except Exception as error:
        _emit(stage, "failed", int((time.perf_counter() - started) * 1000), type(error).__name__)
        raise
    else:
        _emit(stage, "completed", int((time.perf_counter() - started) * 1000))


def admin_settings_call(stage, function, *args, **kwargs):
    with admin_settings_stage(stage):
        return function(*args, **kwargs)


def admin_settings_checkpoint(stage):
    _emit(stage, "checkpoint")


def admin_settings_probe_failure(stage, error_type=None):
    # Avoid settings-backed logging while reporting a potentially blocked settings request.
    _emit(stage, "failed", error_type=error_type, force=True)


def trace_admin_settings(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            return admin_settings_call(stage, function, *args, **kwargs)
        return wrapped
    return decorate


def trace_admin_settings_wsgi(application):
    @wraps(application)
    def wrapped(environ, start_response):
        if environ.get("PATH_INFO") != "/admin/settings" or environ.get("REQUEST_METHOD") != "GET":
            return application(environ, start_response)

        token = _request_id.set(uuid.uuid4().hex)

        def traced_start_response(status, headers, exc_info=None):
            _emit("response_headers", "started")
            result = start_response(status, headers, exc_info)
            status_code = status[:3] if status[:3].isdigit() else "unknown"
            _emit("response_headers", "completed", status_code=status_code)
            return result

        try:
            with admin_settings_stage("wsgi_request"):
                return application(environ, traced_start_response)
        finally:
            _request_id.reset(token)

    return wrapped

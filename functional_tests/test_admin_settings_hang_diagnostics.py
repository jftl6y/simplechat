#!/usr/bin/env python3
# test_admin_settings_hang_diagnostics.py
"""
Functional tests for admin settings hang diagnostics.
Version: 0.261.031
Implemented in: 0.261.030

These tests verify request-scoped stage markers, blocking behavior, failure
reporting, and unchanged responses without connecting to cloud services.
"""

import io
import re
import sys
import threading
import unittest
from pathlib import Path

from flask import Flask, render_template_string


APP_ROOT = Path(__file__).resolve().parents[1] / "application" / "single_app"
sys.path.insert(0, str(APP_ROOT))

import admin_settings_diagnostics as diagnostics


class AdminSettingsHangDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.handler = next(
            handler for handler in diagnostics._logger.handlers
            if getattr(handler, "_admin_settings_diagnostic", False)
        )
        self.previous_stream = self.handler.setStream(self.output)
        self.app = Flask(__name__)
        self.app.testing = True
        self.app.wsgi_app = diagnostics.trace_admin_settings_wsgi(self.app.wsgi_app)

    def tearDown(self):
        self.handler.setStream(self.previous_stream)

    def test_request_hooks_template_and_response_are_correlated(self):
        @self.app.before_request
        @diagnostics.trace_admin_settings("test_before")
        def before():
            return None

        @self.app.context_processor
        @diagnostics.trace_admin_settings("test_context")
        def inject():
            return {"value": "ok"}

        @self.app.route("/admin/settings")
        @diagnostics.trace_admin_settings("test_route")
        def route():
            return diagnostics.admin_settings_call(
                "test_render", render_template_string, "{{ value }}"
            )

        with self.app.test_client() as client:
            response = client.get("/admin/settings")
            body = response.get_data(as_text=True)
        text = self.output.getvalue()
        identifiers = re.findall(r"request_id=([0-9a-f]{32})", text)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body, "ok")
        self.assertTrue(identifiers)
        self.assertEqual(len(set(identifiers)), 1)
        for stage in ("wsgi_request", "test_before", "test_route", "test_render", "test_context", "response_headers"):
            self.assertIn(f"stage={stage} event=started", text)
            self.assertIn(f"stage={stage} event=completed", text)
        self.assertRegex(text, r"stage=test_render event=completed duration_ms=\d+")
        self.assertIn("stage=response_headers event=completed status_code=200", text)

    def test_blocked_operation_logs_start_before_completion(self):
        entered = threading.Event()
        release = threading.Event()
        errors = []

        def blocked():
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test release was not signaled")
            return "finished"

        @self.app.route("/admin/settings")
        def route():
            return diagnostics.admin_settings_call("blocked_operation", blocked)

        def run_request():
            try:
                with self.app.test_client() as client:
                    response = client.get("/admin/settings")
                    response.get_data()
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=run_request)
        worker.start()
        try:
            reached = entered.wait(5)
            text_while_blocked = self.output.getvalue()
        finally:
            release.set()
            worker.join(5)

        self.assertTrue(reached)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertIn("stage=blocked_operation event=started", text_while_blocked)
        self.assertNotIn("stage=blocked_operation event=completed", text_while_blocked)
        self.assertIn("stage=blocked_operation event=completed", self.output.getvalue())

    def test_failure_logs_type_without_exception_content_and_is_reraised(self):
        secret = "sensitive-exception-detail"

        @self.app.route("/admin/settings")
        def route():
            def failing():
                raise ValueError(secret)
            return diagnostics.admin_settings_call("test_failure", failing)

        with self.app.test_client() as client:
            with self.assertRaisesRegex(ValueError, secret):
                client.get("/admin/settings")
        text = self.output.getvalue()
        self.assertIn("stage=test_failure event=failed", text)
        self.assertIn("error_type=ValueError", text)
        self.assertNotIn(secret, text)

    def test_other_paths_and_post_have_no_diagnostic_output(self):
        @self.app.route("/admin/settings", methods=["POST"])
        @diagnostics.trace_admin_settings("test_route")
        def post_route():
            return "saved"

        @self.app.route("/workspace")
        @diagnostics.trace_admin_settings("test_route")
        def other_route():
            return "workspace"

        with self.app.test_client() as client:
            post = client.post("/admin/settings")
            other = client.get("/workspace")
        self.assertEqual(post.status_code, 200)
        self.assertEqual(other.status_code, 200)
        self.assertEqual(self.output.getvalue(), "")

    def test_probe_failure_is_visible_outside_traced_get(self):
        diagnostics.admin_settings_probe_failure("source_review_probe_start", "OSError")
        text = self.output.getvalue()
        self.assertIn("stage=source_review_probe_start event=failed error_type=OSError", text)
        self.assertNotIn("request_id=", text)

    def test_concurrent_requests_have_separate_correlation_ids(self):
        barrier = threading.Barrier(2)
        errors = []

        @self.app.route("/admin/settings")
        @diagnostics.trace_admin_settings("concurrent_route")
        def route():
            barrier.wait(timeout=5)
            return "ok"

        def run_request():
            try:
                with self.app.test_client() as client:
                    response = client.get("/admin/settings")
                    response.get_data()
            except Exception as error:
                errors.append(error)

        workers = [threading.Thread(target=run_request) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)

        lines = self.output.getvalue().splitlines()
        started = [
            re.search(r"request_id=([0-9a-f]{32})", line).group(1)
            for line in lines if "stage=concurrent_route event=started" in line
        ]
        completed = [
            re.search(r"request_id=([0-9a-f]{32})", line).group(1)
            for line in lines if "stage=concurrent_route event=completed" in line
        ]
        self.assertEqual(errors, [])
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(set(started)), 2)
        self.assertCountEqual(started, completed)

    def test_wsgi_failure_before_flask_hooks_is_visible(self):
        def failing_application(environ, start_response):
            raise RuntimeError("private failure")

        application = diagnostics.trace_admin_settings_wsgi(failing_application)
        with self.assertRaises(RuntimeError):
            application(
                {"PATH_INFO": "/admin/settings", "REQUEST_METHOD": "GET"},
                lambda status, headers, exc_info=None: None,
            )
        text = self.output.getvalue()
        self.assertIn("stage=wsgi_request event=started", text)
        self.assertIn("stage=wsgi_request event=failed", text)
        self.assertNotIn("private failure", text)


if __name__ == "__main__":
    unittest.main()

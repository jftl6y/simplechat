#!/usr/bin/env python3
# test_source_review_runtime_probe.py
"""
Functional tests for the isolated Source Review runtime probe.
Version: 0.261.031
Implemented in: 0.261.031

These tests exercise actual subprocesses, deadline enforcement, process-tree
cleanup, capability reporting, and retry behavior without cloud services.
"""

import importlib.util
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from flask import Flask


APP_ROOT = Path(__file__).resolve().parents[1] / "application" / "single_app"
sys.path.insert(0, str(APP_ROOT))

appinsights = types.ModuleType("functions_appinsights")
appinsights.log_event = mock.Mock()
debug = types.ModuleType("functions_debug")
debug.debug_print = mock.Mock()
with mock.patch.dict(sys.modules, {"functions_appinsights": appinsights, "functions_debug": debug}):
    spec = importlib.util.spec_from_file_location("probe_test_source_review", APP_ROOT / "functions_source_review.py")
    source_review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source_review)

PRODUCTION_PROBE_TIMEOUT_SECONDS = source_review._SOURCE_REVIEW_PROBE_TIMEOUT_SECONDS


FAKE_PLAYWRIGHT = """
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(os.environ["PROBE_STATE_DIR"])

def step(name):
    (root / name).write_text("reached")
    if os.environ.get("PROBE_FAIL_STAGE") == name:
        raise RuntimeError("private probe detail")
    if os.environ.get("PROBE_HANG_STAGE") == name:
        if os.environ.get("PROBE_CREATE_CHILD"):
            marker = str(root / "child_survived")
            subprocess.Popen([sys.executable, "-c", "import pathlib,time; time.sleep(3); pathlib.Path(%r).write_text('alive')" % marker])
        time.sleep(30)

step("import")

class Browser:
    def close(self):
        step("close")

class Chromium:
    def launch(self, **kwargs):
        (root / "launch_args").write_text(json.dumps(kwargs))
        step("launch")
        return Browser()

class Context:
    def __enter__(self):
        step("startup")
        self.chromium = Chromium()
        return self
    def __exit__(self, *args):
        step("shutdown")

def sync_playwright():
    return Context()
"""


class SourceReviewRuntimeProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        package = self.root / "playwright"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "sync_api.py").write_text(FAKE_PLAYWRIGHT, encoding="utf-8")
        env = {
            "PYTHONPATH": os.pathsep.join((str(self.root), os.environ.get("PYTHONPATH", ""))),
            "PROBE_STATE_DIR": str(self.root),
            "SOURCE_REVIEW_CHROMIUM_NO_SANDBOX": "true",
        }
        self.environment = mock.patch.dict(os.environ, env)
        self.environment.start()
        self.timeout = mock.patch.object(source_review, "_SOURCE_REVIEW_PROBE_TIMEOUT_SECONDS", 1.5)
        self.timeout.start()
        self.checkpoint = mock.patch.object(source_review, "admin_settings_checkpoint")
        self.checkpoints = self.checkpoint.start()
        source_review._SOURCE_REVIEW_RUNTIME_CAPABILITIES_CACHE = None

    def tearDown(self):
        source_review._SOURCE_REVIEW_RUNTIME_CAPABILITIES_CACHE = None
        self.checkpoint.stop()
        self.timeout.stop()
        self.environment.stop()
        self.directory.cleanup()

    def test_success_and_cached_result(self):
        capabilities = source_review.get_source_review_runtime_capabilities()
        launch = json.loads((self.root / "launch_args").read_text())
        self.assertTrue(capabilities["playwright_available"])
        self.assertTrue(capabilities["js_rendering_available"])
        self.assertTrue(capabilities["chromium_launch_available"])
        self.assertEqual(launch["timeout"], 5000)
        self.assertIn("--no-sandbox", launch["args"])
        self.assertTrue((self.root / "shutdown").exists())
        os.environ["PROBE_FAIL_STAGE"] = "startup"
        cached = source_review.get_source_review_runtime_capabilities()
        self.assertEqual(cached, capabilities)
        refreshed = source_review.get_source_review_runtime_capabilities(force_refresh=True)
        self.assertFalse(refreshed["js_rendering_available"])

    def test_failed_probe_is_safe_and_does_not_expose_exception(self):
        os.environ["PROBE_FAIL_STAGE"] = "launch"
        result = source_review.get_source_review_runtime_capabilities()
        self.assertTrue(result["playwright_available"])
        self.assertFalse(result["js_rendering_available"])
        self.assertNotIn("private probe detail", result["message"])
        self.assertIn("failed", result["message"])
        self.assertFalse(source_review.normalize_source_review_js_rendering_enabled(True, result))

    def test_probe_start_failure_is_reported_and_retryable(self):
        with mock.patch.object(source_review.subprocess, "Popen", side_effect=OSError("private detail")):
            result = source_review.get_source_review_runtime_capabilities()
        self.assertIn("Unable to start", result["message"])
        self.assertNotIn("private detail", result["message"])
        self.assertIsNone(source_review._SOURCE_REVIEW_RUNTIME_CAPABILITIES_CACHE)

    def test_each_playwright_phase_is_bounded_and_retryable(self):
        for phase in ("import", "startup", "launch", "close", "shutdown"):
            with self.subTest(phase=phase):
                os.environ["PROBE_HANG_STAGE"] = phase
                self.checkpoints.reset_mock()
                started = time.monotonic()
                result = source_review.get_source_review_runtime_capabilities(force_refresh=True)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 6)
                self.assertTrue((self.root / phase).exists())
                self.assertTrue(result["playwright_available"])
                self.assertFalse(result["js_rendering_available"])
                self.assertIn("timed out", result["message"])
                self.assertIsNone(source_review._SOURCE_REVIEW_RUNTIME_CAPABILITIES_CACHE)
                self.checkpoints.assert_any_call(f"source_review_probe_{phase}_timed_out")
                (self.root / phase).unlink()
        del os.environ["PROBE_HANG_STAGE"]
        result = source_review.get_source_review_runtime_capabilities()
        self.assertTrue(result["js_rendering_available"])

    def test_production_deadline_is_ten_seconds(self):
        os.environ["PROBE_HANG_STAGE"] = "startup"
        self.assertEqual(PRODUCTION_PROBE_TIMEOUT_SECONDS, 10)
        with mock.patch.object(source_review, "_SOURCE_REVIEW_PROBE_TIMEOUT_SECONDS", PRODUCTION_PROBE_TIMEOUT_SECONDS):
            started = time.monotonic()
            result = source_review.get_source_review_runtime_capabilities()
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 9.5)
        self.assertLess(elapsed, 15)
        self.assertIn("timed out", result["message"])

    def test_timed_out_request_returns_and_kills_descendant(self):
        os.environ["PROBE_HANG_STAGE"] = "startup"
        os.environ["PROBE_CREATE_CHILD"] = "1"
        app = Flask(__name__)

        @app.route("/admin/settings")
        def settings():
            return source_review.get_source_review_runtime_capabilities()["message"]

        started = time.monotonic()
        with app.test_client() as client:
            response = client.get("/admin/settings")
        elapsed = time.monotonic() - started
        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 6)
        self.assertIn(b"timed out", response.data)
        self.assertTrue((self.root / "startup").exists())
        time.sleep(3.2)
        self.assertFalse((self.root / "child_survived").exists())

    def test_missing_playwright_is_reported_without_enabling_rendering(self):
        with mock.patch.object(source_review.subprocess, "Popen") as start:
            start.return_value.wait.return_value = 2
            result = source_review.get_source_review_runtime_capabilities()
        self.assertFalse(result["playwright_available"])
        self.assertFalse(result["js_rendering_available"])
        self.assertIn("not installed", result["message"])


if __name__ == "__main__":
    unittest.main()

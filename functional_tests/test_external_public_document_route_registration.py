# test_external_public_document_route_registration.py
"""
Functional test for external public document route registration.
Version: 0.261.066
Implemented in: 0.261.066

Exercise the saved app registration, real route bodies, and real auth decorators
with Flask's test client. Source isolation avoids cloud bootstrap; token validation,
workspace storage, document storage, and the executor are mocked. This suite does
not verify full application imports, Azure credentials, or document indexing.
"""

import ast
from functools import wraps
from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock
import uuid

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.utils import secure_filename


APP_DIR = Path(__file__).resolve().parents[1] / "application" / "single_app"
UPLOAD_PATH = "/external/public_documents/upload"


def execute_source_nodes(filename, namespace, predicate):
    source_path = APP_DIR / filename
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    selected = ast.Module(
        body=[node for node in tree.body if predicate(node)],
        type_ignores=[],
    )
    exec(compile(selected, str(source_path), "exec"), namespace)


class ExternalPublicDocumentRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.settings = {"enable_public_workspaces": True}
        self.created_paths = []
        self.create_document = Mock(side_effect=self.capture_document)
        self.submit_stored = Mock()
        self.workspace_lookup = Mock(return_value={"id": "workspace-123"})
        self.workspace_role = Mock(return_value="Owner")
        self.validate_token = Mock(return_value=(True, {"roles": ["ExternalApi"]}))
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.extensions["executor"] = SimpleNamespace(submit_stored=self.submit_stored)
        self.namespace = {
            "__name__": __name__,
            "app": self.app,
            "Blueprint": Blueprint,
            "current_app": current_app,
            "jsonify": jsonify,
            "request": request,
            "wraps": wraps,
            "os": os,
            "tempfile": tempfile,
            "uuid": uuid,
            "secure_filename": secure_filename,
            "get_settings": lambda: self.settings,
            "validate_bearer_token": self.validate_token,
            "debug_print": Mock(),
            "log_event": Mock(),
            "swagger_route": lambda **kwargs: lambda function: function,
            "get_auth_security": lambda: [],
            "find_public_workspace_by_id": self.workspace_lookup,
            "get_user_role_in_public_workspace": self.workspace_role,
            "check_public_workspace_status_allows_operation": Mock(return_value=(True, "")),
            "allowed_file": lambda filename: filename.endswith(".txt"),
            "create_document": self.create_document,
            "update_document": Mock(),
            "process_document_upload_background": Mock(),
        }
        auth_names = {
            "apply_blueprint_auth",
            "external_api_required_blueprint",
            "accesstoken_required",
        }
        execute_source_nodes(
            "functions_authentication.py",
            self.namespace,
            lambda node: isinstance(node, ast.FunctionDef) and node.name in auth_names,
        )
        execute_source_nodes(
            "functions_settings.py",
            self.namespace,
            lambda node: isinstance(node, ast.FunctionDef) and node.name == "enabled_required",
        )
        execute_source_nodes(
            "route_external_public_documents.py",
            self.namespace,
            lambda node: not isinstance(node, (ast.Import, ast.ImportFrom)),
        )
        execute_source_nodes("app.py", self.namespace, self.is_registration_node)
        self.client = self.app.test_client()
        self.form_data = {
            "user_id": "user-123",
            "active_workspace_id": "workspace-123",
            "classification": "N/A",
        }
        self.headers = {"Authorization": "Bearer test-token"}
        self.addCleanup(self.cleanup_uploads)

    @staticmethod
    def is_registration_node(node):
        if isinstance(node, ast.FunctionDef):
            return node.name == "register_route_blueprint"
        return (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "register_route_blueprint"
            and bool(node.value.args)
            and isinstance(node.value.args[0], ast.Constant)
            and node.value.args[0].value == "external_public_documents"
        )

    def capture_document(self, **kwargs):
        self.created_paths.append(Path(kwargs["source_file_path"]))

    def cleanup_uploads(self):
        for path in self.created_paths:
            path.unlink(missing_ok=True)

    def test_registered_routes_require_external_api_bearer_role(self):
        routes = [
            ("POST", UPLOAD_PATH),
            ("GET", "/external/public_documents"),
            ("GET", "/external/public_documents/document-123"),
            ("PATCH", "/external/public_documents/document-123"),
            ("DELETE", "/external/public_documents/document-123"),
            ("POST", "/external/public_documents/document-123/extract_metadata"),
            ("POST", "/external/public_documents/upgrade_legacy"),
        ]
        auth_cases = [
            ({}, (True, {"roles": ["ExternalApi"]}), 401),
            ({"Authorization": "Basic invalid"}, (True, {}), 401),
            (self.headers, (False, "Invalid token"), 401),
            (self.headers, (True, {"roles": ["User"]}), 403),
        ]
        for method, path in routes:
            for headers, validation_result, expected_status in auth_cases:
                with self.subTest(method=method, path=path, status=expected_status):
                    self.validate_token.return_value = validation_result
                    response = self.client.open(path, method=method, headers=headers)
                    self.assertEqual(response.status_code, expected_status)
        self.workspace_lookup.assert_not_called()
        self.create_document.assert_not_called()

    def test_authorized_upload_reaches_workspace_validation(self):
        self.workspace_lookup.return_value = None
        response = self.client.post(UPLOAD_PATH, headers=self.headers, data=self.form_data)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "Public workspace not found"})
        self.workspace_lookup.assert_called_once_with("workspace-123")
        self.create_document.assert_not_called()

    def test_disabled_workspaces_and_insufficient_membership_reject_upload(self):
        self.settings["enable_public_workspaces"] = False
        disabled = self.client.post(UPLOAD_PATH, headers=self.headers, data=self.form_data)
        self.assertEqual(disabled.status_code, 400)
        self.assertIn("disabled", disabled.get_json()["error"].lower())
        self.workspace_lookup.assert_not_called()

        self.settings["enable_public_workspaces"] = True
        for role in (None, "User"):
            with self.subTest(role=role):
                self.workspace_role.return_value = role
                denied = self.client.post(UPLOAD_PATH, headers=self.headers, data=self.form_data)
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied.get_json(), {"error": "Access denied"})
        self.create_document.assert_not_called()
        self.submit_stored.assert_not_called()

    def test_authorized_multipart_upload_queues_document(self):
        response = self.client.post(
            UPLOAD_PATH,
            headers=self.headers,
            data={**self.form_data, "file": (BytesIO(b"Upload regression"), "sample.txt")},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["processed_filenames"], ["sample.txt"])
        self.assertEqual(len(payload["document_ids"]), 1)
        self.create_document.assert_called_once()
        document = self.create_document.call_args.kwargs
        self.assertEqual(document["document_id"], payload["document_ids"][0])
        self.assertEqual(document["public_workspace_id"], "workspace-123")
        self.assertEqual(document["user_id"], "user-123")
        self.assertEqual(document["status"], "Queued for processing")
        self.assertEqual(self.created_paths[0].read_bytes(), b"Upload regression")
        self.submit_stored.assert_called_once_with(
            document["document_id"],
            self.namespace["process_document_upload_background"],
            document_id=document["document_id"],
            public_workspace_id="workspace-123",
            user_id="user-123",
            temp_file_path=document["source_file_path"],
            original_filename="sample.txt",
        )


if __name__ == "__main__":
    unittest.main()

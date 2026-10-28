# test_msgraph_commercial_mail_action.py
"""
Regression tests for the commercial Microsoft Graph mail action.
Version: 0.261.033
Implemented in: 0.261.033

These tests verify commercial actions only read mail or create unsent drafts,
and never send their delegated token to an untrusted Graph endpoint.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


APP_DIR = Path(__file__).resolve().parents[1] / "application" / "single_app"


@pytest.fixture
def graph_plugin(monkeypatch):
    monkeypatch.syspath_prepend(str(APP_DIR))

    def install_module(name, **values):
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    def decorator(*args, **kwargs):
        return lambda function: function

    commercial = install_module(
        "commercial_graph_connection",
        commercial_graph_token=lambda scopes: {"access_token": "test-token"},
    )
    install_module(
        "functions_authentication",
        get_current_user_info=lambda: {"userId": "user-1"},
        get_valid_access_token_for_plugins=lambda scopes: {"access_token": "ordinary-token"},
    )
    install_module("functions_debug", debug_print=lambda *args: None)
    install_module(
        "functions_group",
        assert_group_role=lambda *args, **kwargs: None,
        find_group_by_id=lambda *args: None,
        require_active_group=lambda *args: None,
    )
    install_module("semantic_kernel", __path__=[])
    install_module("semantic_kernel.functions", __path__=[], kernel_function=decorator)
    install_module("semantic_kernel.functions.kernel_plugin", KernelPlugin=object)
    install_module("semantic_kernel_plugins", __path__=[str(APP_DIR / "semantic_kernel_plugins")])
    install_module("semantic_kernel_plugins.base_plugin", BasePlugin=type("BasePlugin", (), {"__init__": lambda self, manifest: None}))
    install_module("semantic_kernel_plugins.plugin_invocation_logger", plugin_function_logger=decorator)
    pending_names = {
        "MSGRAPH_PENDING_ACTION_DELAYED": "delayed",
        "MSGRAPH_PENDING_ACTION_MANUAL": "manual",
        "MSGRAPH_PENDING_OPERATION_CREATE_CALENDAR_INVITE": "create_calendar_invite",
        "MSGRAPH_PENDING_OPERATION_SEND_MAIL": "send_mail",
        "MSGRAPH_PENDING_RESOURCE_CALENDAR": "calendar",
        "MSGRAPH_PENDING_RESOURCE_MAIL": "mail",
        "MSGRAPH_PENDING_STATUS_PENDING": "pending",
        "MSGRAPH_PENDING_STATUS_SCHEDULED": "scheduled",
    }
    for name in (
        "build_calendar_pending_action_summary",
        "build_mail_pending_action_summary",
        "create_msgraph_pending_action",
        "sanitize_msgraph_pending_action_for_client",
        "schedule_msgraph_pending_action_auto_commit",
    ):
        pending_names[name] = lambda *args, **kwargs: pytest.fail("Commercial mail must not create pending send actions")
    install_module("functions_msgraph_pending_actions", **pending_names)

    spec = importlib.util.spec_from_file_location(
        "commercial_mail_graph_plugin_test",
        APP_DIR / "semantic_kernel_plugins" / "msgraph_plugin.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, commercial


class FakeResponse:
    status_code = 200
    headers = {}
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_commercial_mail_is_limited_to_read_and_drafts(graph_plugin, monkeypatch):
    module, commercial = graph_plugin
    calls = []
    scopes_used = []
    monkeypatch.setattr(commercial, "commercial_graph_token", lambda scopes: {"access_token": "test-token"})
    monkeypatch.setattr(module, "commercial_graph_token", lambda scopes: scopes_used.append(scopes) or {"access_token": "test-token"})

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse({"id": "draft-id"} if method == "POST" else {"value": []})

    monkeypatch.setattr(module.requests, "request", request)
    plugin = module.MSGraphPlugin({
        "additionalFields": {
            "msgraph_connection": "commercial_mail",
            "msgraph_mail_send_mode": "auto_send",
        },
        "scopes": {"send_mail": ["Mail.Send"]},
    })
    functions = set(plugin.get_functions())
    metadata = plugin.metadata
    messages = plugin.get_my_messages()
    draft = plugin.send_mail("recipient@example.com", "Subject", "Body")

    assert functions == {"get_my_messages", "send_mail"}
    send_spec = next(method for method in metadata["methods"] if method["name"] == "send_mail")
    assert "unsent draft" in send_spec["description"]
    assert messages["count"] == 0
    assert "bodyPreview" in calls[0][2]["params"]["$select"]
    assert draft["mail_send_status"] == "draft_created"
    assert calls[1][1] == "https://graph.microsoft.com/v1.0/me/messages"
    assert all(call[2]["allow_redirects"] is False for call in calls)
    assert scopes_used == [["Mail.Read"], ["Mail.ReadWrite"]]


def test_commercial_mail_rejects_untrusted_endpoint_and_continuation(graph_plugin, monkeypatch):
    module, _ = graph_plugin
    calls = []

    def request(method, url, **kwargs):
        calls.append(url)
        return FakeResponse({"value": [], "@odata.nextLink": "https://other.example/collect"})

    monkeypatch.setattr(module.requests, "request", request)
    plugin = module.MSGraphPlugin({"additionalFields": {"msgraph_connection": "commercial_mail"}})
    result = plugin.get_my_messages()
    invalid_endpoint = module.MSGraphPlugin({
        "endpoint": "https://other.example",
        "additionalFields": {"msgraph_connection": "commercial_mail"},
    }).get_my_messages()
    blocked_operation = plugin.mark_message_as_read("message")

    assert result["error"] == "invalid_parameters"
    assert invalid_endpoint["error"] == "invalid_parameters"
    assert blocked_operation["error"] == "invalid_parameters"
    assert calls == ["https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"]


def test_existing_graph_connection_uses_existing_token_provider(graph_plugin, monkeypatch):
    module, _ = graph_plugin
    requested_scopes = []
    requests = []
    monkeypatch.setattr(
        module,
        "get_valid_access_token_for_plugins",
        lambda scopes: requested_scopes.append(scopes) or {"access_token": "ordinary-token"},
    )
    monkeypatch.setattr(
        module.requests,
        "request",
        lambda method, url, **kwargs: requests.append(kwargs) or FakeResponse({"value": []}),
    )
    plugin = module.MSGraphPlugin({})
    result = plugin.get_my_messages()

    assert result["count"] == 0
    assert requested_scopes == [["Mail.Read"]]
    assert "allow_redirects" not in requests[0]
    assert "bodyPreview" not in requests[0]["params"]["$select"]
    assert "get_my_events" in plugin.get_functions()


def test_commercial_mail_can_request_full_message_body(graph_plugin, monkeypatch):
    module, _ = graph_plugin
    calls = []
    monkeypatch.setattr(
        module.requests,
        "request",
        lambda method, url, **kwargs: calls.append(kwargs) or FakeResponse({"value": []}),
    )
    plugin = module.MSGraphPlugin({"additionalFields": {"msgraph_connection": "commercial_mail"}})
    plugin.get_my_messages(select_fields="id,subject,body")
    assert calls[0]["params"]["$select"] == "id,subject,body"


def test_unknown_connection_type_fails_closed(graph_plugin, monkeypatch):
    module, _ = graph_plugin
    monkeypatch.setattr(
        module,
        "get_valid_access_token_for_plugins",
        lambda scopes: pytest.fail("Unknown connections must not use the regular token provider"),
    )
    plugin = module.MSGraphPlugin({"additionalFields": {"msgraph_connection": "unknown"}})
    functions = plugin.get_functions()
    result = plugin.get_my_messages()

    assert functions == []
    assert result["error"] == "invalid_parameters"


def test_missing_commercial_connection_does_not_use_government_token(graph_plugin, monkeypatch):
    module, _ = graph_plugin
    monkeypatch.setattr(
        module,
        "commercial_graph_token",
        lambda scopes: {"error": "commercial_connection_required", "message": "Connect your commercial mailbox."},
    )
    monkeypatch.setattr(
        module,
        "get_valid_access_token_for_plugins",
        lambda scopes: pytest.fail("Commercial mail must not fall back to the SimpleChat token"),
    )
    monkeypatch.setattr(
        module.requests,
        "request",
        lambda *args, **kwargs: pytest.fail("No Graph request should be sent without a commercial token"),
    )
    plugin = module.MSGraphPlugin({"additionalFields": {"msgraph_connection": "commercial_mail"}})
    result = plugin.get_my_messages()

    assert result["error"] == "commercial_connection_required"


def test_commercial_draft_rejects_redirects_and_missing_identifier(graph_plugin, monkeypatch):
    module, _ = graph_plugin
    response = FakeResponse({"id": "not-a-draft"})
    response.status_code = 302
    monkeypatch.setattr(module.requests, "request", lambda *args, **kwargs: response)
    plugin = module.MSGraphPlugin({"additionalFields": {"msgraph_connection": "commercial_mail"}})
    redirected = plugin.send_mail("recipient@example.com", "Subject")
    response.status_code = 200
    response.payload = {}
    missing_id = plugin.send_mail("recipient@example.com", "Subject")

    assert redirected["error"] == "invalid_parameters"
    assert missing_id["error"] == "invalid_parameters"
    assert "Check Outlook" in missing_id["message"]

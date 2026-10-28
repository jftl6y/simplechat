# test_commercial_mail_profile.py
"""
UI regression test for the commercial mail connection controls.
Version: 0.261.033
Implemented in: 0.261.033

This test exercises connection status, disconnect and invalid authorization URLs
using the actual profile card and local browser script.
"""

import os
import re
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "application" / "single_app" / "templates" / "profile.html"
SCRIPT = ROOT / "application" / "single_app" / "static" / "js" / "profile" / "commercial-mail.js"
MODAL_SCRIPT = ROOT / "application" / "single_app" / "static" / "js" / "plugin_modal_stepper.js"


@pytest.mark.ui
def test_commercial_mail_status_disconnect_and_invalid_url():
    template = TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r'<div class="section-card" id="commercial-mail">.*?</div>\s*(?={% set font_size_preference)', template, re.S)
    if not match:
        raise AssertionError("Commercial mail profile controls were not found.")

    state = {"connected": True, "auth_url": "https://untrusted.example/authorize", "connect_error": None}

    with playwright.sync_playwright() as browser_tools:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        if not executable and os.name == "nt":
            edge = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if edge.exists():
                executable = str(edge)
        browser = browser_tools.chromium.launch(**({"executable_path": executable} if executable else {}))
        page = browser.new_page()
        page.route("**/profile**", lambda route: route.fulfill(status=200, body=match.group(0), content_type="text/html"))

        def handle_api(route):
            path = route.request.url.rsplit("/", 1)[-1]
            if path == "status":
                route.fulfill(json={"configured": True, "connected": state["connected"], "account": "mail@example.com"})
            elif path == "disconnect":
                state["connected"] = False
                route.fulfill(json={"success": True})
            elif path == "connect":
                if state["connect_error"]:
                    route.fulfill(status=503, json={"message": state["connect_error"]})
                else:
                    route.fulfill(json={"auth_url": state["auth_url"]})
            else:
                route.abort()

        page.route("**/api/commercial-graph/**", handle_api)
        page.goto("http://simplechat.test/profile?tab=settings&commercial_mail_error=1#commercial-mail")
        page.add_script_tag(path=str(SCRIPT))
        page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
        playwright.expect(page.locator("#commercial-mail-status")).to_contain_text("sign-in failed")
        assert "commercial_mail_error" not in page.url
        page.goto("http://simplechat.test/profile")
        page.add_script_tag(path=str(SCRIPT))
        page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
        playwright.expect(page.locator("#commercial-mail-status")).to_contain_text("mail@example.com")
        page.locator("#commercial-mail-disconnect").click()
        playwright.expect(page.locator("#commercial-mail-status")).to_contain_text("No commercial account connected.")
        state["connect_error"] = "Your session has expired. Sign in again."
        page.locator("#commercial-mail-connect").click()
        playwright.expect(page.locator("#commercial-mail-status")).to_contain_text(state["connect_error"])
        state["connect_error"] = None
        page.locator("#commercial-mail-connect").click()
        playwright.expect(page.locator("#commercial-mail-status")).to_contain_text("Invalid sign-in URL.")
        assert page.url == "http://simplechat.test/profile"
        state["auth_url"] = "https://login.microsoftonline.com/test-tenant/authorize"
        page.route("https://login.microsoftonline.com/**", lambda route: route.fulfill(status=200, body="Sign-in"))
        with page.expect_navigation():
            page.locator("#commercial-mail-connect").click()
        assert page.url == state["auth_url"]

        page.set_content("""
            <style>.d-none { display: none !important; }</style>
            <select id="msgraph-connection-type">
                <option value="signed_in">Signed in</option>
                <option value="commercial_mail">Commercial</option>
            </select>
            <div id="msgraph-capabilities-list"></div>
        """)
        source = MODAL_SCRIPT.read_text(encoding="utf-8")
        source = re.sub(r"^import .*?;\s*$", "", source, flags=re.MULTILINE)
        source = source.replace("export class PluginModalStepper", "class PluginModalStepper")
        page.add_script_tag(content=source + "\nwindow.CommercialTestStepper = PluginModalStepper;")
        page.evaluate("""() => {
            window.stepper = Object.create(window.CommercialTestStepper.prototype);
            window.stepper.setMsGraphCapabilities();
            document.getElementById('msgraph-connection-type').addEventListener(
                'change', () => window.stepper.updateMsGraphConnectionVisibility()
            );
        }""")
        page.locator("#msgraph-connection-type").select_option("commercial_mail")
        playwright.expect(page.get_by_text("Create mail drafts", exact=True)).to_be_visible()
        playwright.expect(page.locator("#msgraph-capability-get_my_messages")).to_be_visible()
        playwright.expect(page.locator("#msgraph-capability-create_calendar_invite")).to_be_hidden()
        playwright.expect(page.locator("#msgraph-mail-send-mode")).to_be_hidden()
        page.locator("#msgraph-connection-type").select_option("signed_in")
        playwright.expect(page.locator("#msgraph-calendar-send-mode")).to_be_visible()
        playwright.expect(page.locator("#msgraph-mail-send-mode")).to_be_visible()
        browser.close()

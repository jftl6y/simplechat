# test_admin_walkthrough_model_endpoints.py
"""
UI regression test for setup walkthrough model endpoints.
Version: 0.261.032
Implemented in: 0.261.032

Validates navigation and live validation using the application walkthrough code.
"""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ADMIN_JS = ROOT / "application" / "single_app" / "static" / "js" / "admin" / "admin_settings.js"
ENDPOINT_JS = ROOT / "application" / "single_app" / "static" / "js" / "admin" / "admin_model_endpoints.js"
TEMPLATE = ROOT / "application" / "single_app" / "templates" / "admin_settings.html"


def extract_function(source, name):
    start = source.index(f"function {name}(")
    end = source.index("\n}", start)
    return source[start:end + 2]


@pytest.mark.ui
def test_walkthrough_tracks_enabled_endpoint_and_model():
    source = ADMIN_JS.read_text(encoding="utf-8")
    endpoint_source = ENDPOINT_JS.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    steps = template[template.index('<div id="walkthrough-step-2"'):template.index('<!-- Step 4: Workspaces -->')]
    steps += "".join(
        f'<div id="walkthrough-step-{step}" class="walkthrough-step" style="display: none;">Step {step}</div>'
        for step in [1, *range(4, 13)]
    )
    functions = [
        "getCurrentWalkthroughStep",
        "calculateAvailableWalkthroughSteps",
        "shouldSkipWalkthroughStep",
        "findNextApplicableStep",
        "findPreviousApplicableStep",
        "navigateToWalkthroughStep",
        "isWalkthroughMultiEndpointEnabled",
        "getWalkthroughModelEndpoints",
        "isWalkthroughEndpointConfigured",
        "hasWalkthroughEnabledModel",
        "isStepComplete",
        "updateStepCompletionStatus",
        "setupWalkthroughFieldListeners",
        "updateWalkthroughModelStep",
        "validateAndMoveToNextStep",
    ]
    script = "\n".join(extract_function(source, name) for name in functions)
    script += "\n" + extract_function(endpoint_source, "updateHiddenInput")
    browser_path = os.environ.get("SIMPLECHAT_UI_BROWSER_EXECUTABLE")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**({"executable_path": browser_path} if browser_path else {}))
        try:
            page = browser.new_page()
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.set_content(
                '<div id="settings-walkthrough-container">'
                f'{steps}</div><div id="walkthrough-progress"></div>'
                '<button id="walkthrough-prev-btn">Previous</button>'
                '<button id="walkthrough-next-btn">Next</button>'
                '<button id="walkthrough-finish-btn">Finish</button>'
                '<input type="hidden" id="model_endpoints_json">'
            )
            page.add_script_tag(content="""
                window.enableMultiModelEndpoints = true;
                window.modelEndpoints = [];
                let modelEndpoints = [];
                const endpointsInput = document.getElementById('model_endpoints_json');
                const gptSelected = [];
                function handleTabNavigation() {}
                function checkOptionalFeaturesEnabled() { return false; }
                function syncWalkthroughToggles() {}
                function initializeTooltips() {}
            """ + script)
            page.evaluate("""
                () => {
                    setupWalkthroughFieldListeners();
                    document.getElementById('walkthrough-next-btn').onclick = () =>
                        validateAndMoveToNextStep(getCurrentWalkthroughStep());
                    navigateToWalkthroughStep(2);
                }
            """)
            next_button = page.locator("#walkthrough-next-btn")
            expect(next_button).to_be_disabled()
            endpoint = {
                "enabled": True,
                "connection": {"endpoint": "https://example.test"},
                "models": [],
            }
            page.evaluate(
                "(endpoint) => { modelEndpoints = [endpoint]; updateHiddenInput(); }",
                endpoint,
            )
            expect(next_button).to_be_enabled()
            expect(page.locator("#walkthrough-step-2 .alert-success")).to_be_visible()
            next_button.click()
            expect(page.locator("#walkthrough-step-3")).to_be_visible()
            expect(next_button).to_be_disabled()
            endpoint["models"] = [{"deploymentName": "chat-model", "enabled": True}]
            page.evaluate(
                "(endpoint) => { modelEndpoints = [endpoint]; updateHiddenInput(); }",
                endpoint,
            )
            expect(next_button).to_be_enabled()
            endpoint["models"][0]["enabled"] = False
            page.evaluate(
                "(endpoint) => { modelEndpoints = [endpoint]; updateHiddenInput(); }",
                endpoint,
            )
            expect(next_button).to_be_disabled()
            expect(page.locator("#walkthrough-step-3 .alert-danger")).to_be_visible()
            endpoint["models"][0]["enabled"] = True
            page.evaluate(
                "(endpoint) => { modelEndpoints = [endpoint]; updateHiddenInput(); }",
                endpoint,
            )
            next_button.click()
            expect(page.locator("#walkthrough-step-4")).to_be_visible()
            assert json.loads(page.locator("#model_endpoints_json").input_value()) == [endpoint]
            assert not page_errors
        finally:
            browser.close()

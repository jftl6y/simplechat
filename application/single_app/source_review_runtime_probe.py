# source_review_runtime_probe.py
"""Isolated Playwright lifecycle probe for Source Review."""

import importlib.util
import json
import sys


def main() -> int:
    if importlib.util.find_spec("playwright") is None:
        return 2
    print("PLAYWRIGHT_AVAILABLE", flush=True)
    print("PHASE_IMPORT", flush=True)
    from playwright.sync_api import sync_playwright

    print("PHASE_STARTUP", flush=True)
    with sync_playwright() as playwright_instance:
        print("PHASE_LAUNCH", flush=True)
        browser = playwright_instance.chromium.launch(
            headless=True,
            args=json.loads(sys.argv[1]),
            timeout=5000,
        )
        print("PHASE_CLOSE", flush=True)
        browser.close()
        print("PHASE_SHUTDOWN", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"ERROR_TYPE={type(error).__name__}", flush=True)
        sys.exit(1)

# Admin Settings Source Review Probe Hang Fix

Fixed in version: **0.261.031** (`application/single_app/config.py`).

## Issue and root cause

Admin Settings checks whether Playwright Chromium can start, even when Source Review is not in use. The previous five-second Chromium launch timeout did not bound Playwright startup, browser closure, or driver shutdown, so a stalled probe could block both opening and saving Admin Settings.

## Fix

The browser check runs in an isolated process with a ten-second deadline. On timeout, the app attempts to terminate the probe process tree and reports that JavaScript rendering could not be verified. Cleanup errors are recorded in the diagnostic log. The probe records its last stage in the temporary admin settings diagnostic log. A timed-out probe is not cached, so a later request can retry. Successful and ordinary failed checks retain the existing per-process cache behavior.

The existing safety gate remains in effect: when runtime support is not verified, JavaScript rendering is shown as unavailable and saving Admin Settings can turn the saved JavaScript-rendering setting off. Other Source Review functionality is not disabled by this probe.

## Validation

`functional_tests/test_source_review_runtime_probe.py` exercises successful, unavailable, failed, and blocked probes, including child-process cleanup and retry after timeout. The deployed environment must still be checked after deployment: look for `source_review_probe_timed_out` and the phase-specific diagnostic marker, then confirm that `/admin/settings` loads.

# External Public Document Route Registration Fix

Fixed in version: **0.261.066**

## Issue and root cause

The bulkloader posts public-workspace files to
`/external/public_documents/upload`. The external public document route module
already defined this endpoint, but `app.py` neither imported nor called its
registrar. Consequently, Flask had no matching upload route. The refreshed
upstream checkout still had this missing registration.

An isolated Flask multipart request reproduced HTTP 404 before this fix. An
empty HTTP 502 from a deployed instance is a separate observation: missing route
registration alone does not establish why a gateway returned 502. Deployment
startup and request logs are still needed if that response persists.

## Changes and impact

- `application/single_app/app.py` imports the existing registrar and registers
  `external_public_documents` using the shared `external_api_required_blueprint`
  guard. Existing URLs are unchanged.
- The Blueprint requires a valid bearer token with the `ExternalApi` application
  role. Existing route-level bearer checks, the public-workspaces feature gate,
  workspace membership checks, and operation restrictions remain in place.
- Registration also makes the existing list, document read/update/delete,
  metadata extraction, and legacy upgrade endpoints reachable. No route handler
  or bulkloader behavior is changed.
- `functional_tests/route_tests/test_route_blueprint_policy_inventory.py`
  classifies the Blueprint and checks its explicit import and guarded
  registration.
- `application/single_app/config.py` advances the application version from
  `0.261.065` to `0.261.066`.

## Testing and validation

`functional_tests/test_external_public_document_route_registration.py` exercises
the app's selected registration statements and actual route/authentication
functions with Flask's test client. It covers:

- Missing, malformed, invalid, and insufficient-role bearer credentials across
  all existing external public document routes.
- An authorized upload reaching workspace validation instead of an unmatched
  Flask route.
- Rejection when public workspaces are disabled or upload membership is absent
  or insufficient.
- Multipart upload acceptance, document creation arguments, file contents, and
  background-job submission.

The tests isolate source from application bootstrap and mock token validation,
workspace/document storage, and the executor. They do not prove full application
startup, live Azure token validation, actual background processing, or indexing.

Run the focused suite and route policy checks:

```powershell
python .\functional_tests\test_external_public_document_route_registration.py
python -O .\functional_tests\test_external_public_document_route_registration.py
python .\functional_tests\route_tests\test_route_blueprint_policy_inventory.py
python .\functional_tests\route_tests\test_route_unauthenticated_policy_contract.py
python .\functional_tests\route_tests\test_route_policy_test_coverage.py
```

Local validation passed: **4/4** HTTP tests in normal Python, **4/4** in optimized
Python, and **7/7**, **4/4**, and **2/2** checks in the three route-policy scripts,
respectively. Syntax compilation of the changed Python files and Git whitespace
checks also passed.

## Deployment verification

Deploy the updated application to the same App Service/slot used by the
bulkloader. Confirm the application starts successfully, then retry with a valid
`ExternalApi` token and an authorized workspace user. Upload acceptance means the
file was queued; confirm processing status separately.

If HTTP 502 persists, correlate that POST's timestamp with the incoming request
and exception telemetry and App Service/Gunicorn logs. Check for worker exits or
initialization failures before changing upload timeouts or credentials.

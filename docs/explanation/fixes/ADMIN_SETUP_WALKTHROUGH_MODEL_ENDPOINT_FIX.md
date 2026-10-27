# Admin Setup Walkthrough Model Endpoint Fix

Fixed in version: **0.261.032**. The application version is maintained in `application/single_app/config.py`.

## Issue and root cause

The setup walkthrough could block on GPT configuration even when an enabled model endpoint and chat model were already working. Its model steps and first-time setup check only inspected the legacy GPT settings, not the multi-endpoint configuration.

## Changes and impact

The walkthrough now checks enabled endpoints and models when multi-endpoint management is active, and continues to use legacy settings otherwise. Endpoint changes refresh the current walkthrough step, and the instructions describe the current model-endpoint workflow. These checks verify configuration, not connectivity or authorization; use the endpoint's connection test to verify those separately.

## Validation

The regression tests cover enabled and disabled endpoints and models, legacy direct and APIM settings, and progression through the browser walkthrough:

- `functional_tests/test_admin_walkthrough_model_endpoints.js`
- `ui_tests/test_admin_walkthrough_model_endpoints.py`

# Commercial Graph cryptography dependency fix

Fixed in version: **0.261.034**. The application version is tracked in
`application/single_app/config.py`.

The commercial mail change pinned `cryptography` 47.0.0, which requires CFFI
2 or later on Python 3.12. The existing Snowflake connector 3.18.1 requires
CFFI below 2. Pip therefore could not resolve the application's requirements,
and the container build stopped before installing the application.

The application now pins `cryptography` 45.0.7, which is compatible with both
the Snowflake connector and the existing MSAL version. This retains Fernet
support for encrypted commercial mail token records without changing the
Snowflake connector.

`functional_tests/test_cryptography_snowflake_dependency_compatibility.py`
checks installed package metadata against the manifest and the installed CFFI
version. The commercial Graph connection tests continue to exercise encryption
and session behavior. A container build should still be run in the target CI
environment to verify its complete platform-specific installation.

# Docker publishing fixes (0.261.029)

Fixed in version: **0.261.028**

## Issue and root cause

The main Docker publishing workflow authenticated with `MAIN_ACR_LOGIN_SERVER`
but built image names using `ACR_LOGIN_SERVER`. When the latter secret was absent,
Docker received an invalid name beginning with `/simple-chat`.

## Changes

- `.github/workflows/docker_image_publish.yml` now uses
  `MAIN_ACR_LOGIN_SERVER` for both authentication and image publishing.
- The build step passes the secret through an environment variable, checks it
  before building, and stops on the first failed command.
- Both the dated branch/run tag and `latest` are preserved, as are the repository
  root build context and `application/single_app/Dockerfile`.
- `application/single_app/config.py` records version `0.261.028`.

## Validation

Run `python functional_tests/test_docker_publish_registry_secret.py`.
The regression tests check YAML configuration, matching registry secrets, the
missing-secret guard, and the build/tag/push commands. They do not authenticate
to Azure or build an image; a new GitHub Actions run on the private runner is
required to validate registry connectivity and actual publishing.

## App Service manifest compatibility

Fixed in version: **0.261.029**

App Service in the affected deployment rejected an image published with an OCI
image index and OCI manifests. Inspection also showed an attestation manifest
with platform `unknown/unknown`; that entry is metadata, not a runnable image.

The main publishing workflow now uses a per-run Buildx `docker-container` builder
and exports directly to the registry with `oci-mediatypes=false`. It explicitly
targets `linux/amd64` and disables provenance and SBOM attestations. Both the
dated branch/run tag and `latest` are published by the same build. The builder
is removed on exit without changing the self-hosted runner's selected builder.
The runner must have Docker Buildx installed and support the `docker-container`
driver. No other Docker publishing workflows are changed.

The version in `application/single_app/config.py` is updated to `0.261.029`.
The four tests in `functional_tests/test_docker_publish_registry_secret.py` pass,
covering the registry secret, fail-fast guard, export flags, tags, and builder
cleanup configuration. They validate workflow configuration, not Docker runtime
behavior or Azure connectivity.

After running the workflow, inspect the dated tag with
`docker buildx imagetools inspect <registry>/simple-chat:<tag>`. Confirm Docker
schema 2 media types rather than OCI media types, then configure App Service to
use that tag and restart it. The real registry export and App Service pull must
be validated in the deployment environment.

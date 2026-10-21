# Docker publish registry secret fix

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

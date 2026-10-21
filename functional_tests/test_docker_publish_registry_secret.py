# test_docker_publish_registry_secret.py
"""
Functional regression tests for Docker publishing registry configuration.
Version: 0.261.029
Implemented in: 0.261.028

Validate matching registry secrets and Docker-compatible publishing (0.261.029).
Requires PyYAML.
"""

from pathlib import Path
import unittest

import yaml


class DockerPublishRegistrySecretTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github" / "workflows" / "docker_image_publish.yml"
        )
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        cls.steps = workflow["jobs"]["build"]["steps"]
        cls.login = next(
            step for step in cls.steps
            if step.get("name") == "Azure Container Registry Login"
        )
        cls.build = next(
            step for step in cls.steps
            if step.get("name") == "Build and push the Docker image"
        )

    def test_login_and_build_use_same_registry_secret(self):
        expected = "${{ secrets.MAIN_ACR_LOGIN_SERVER }}"
        self.assertEqual(self.login["with"]["login-server"], expected)
        self.assertEqual(self.build["env"]["ACR_LOGIN_SERVER"], expected)

    def test_missing_secret_fails_before_building(self):
        script = self.build["run"]
        self.assertEqual(self.build["shell"], "bash")
        self.assertIn("set -euo pipefail", script)
        guard = ': "${ACR_LOGIN_SERVER:?MAIN_ACR_LOGIN_SERVER secret is missing or empty}"'
        self.assertIn(guard, script)
        self.assertLess(script.index(guard), script.index("docker buildx"))

    def test_both_tags_are_built_and_pushed(self):
        script = self.build["run"]
        self.assertIn('IMAGE="${ACR_LOGIN_SERVER}/simple-chat"', script)
        self.assertIn(
            'TAG="$(date +\'%Y-%m-%d\')_${BRANCH_TAG}_${GITHUB_RUN_NUMBER}"',
            script,
        )
        script = script.replace("\\\n", "")
        commands = [
            " ".join(line.split()) for line in script.splitlines()
            if line.startswith("docker ")
        ]
        self.assertEqual(commands, [
            'docker buildx create --name "$BUILDER" --driver docker-container',
            'docker buildx build --builder "$BUILDER" --platform linux/amd64 '
            '--provenance=false --sbom=false '
            '--file application/single_app/Dockerfile '
            '--output "type=registry,oci-mediatypes=false" '
            '--tag "${IMAGE}:${TAG}" --tag "${IMAGE}:latest" .',
        ])

    def test_builder_is_isolated_and_cleaned_up(self):
        script = self.build["run"]
        self.assertIn('BUILDER="simplechat-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"', script)
        cleanup = 'trap \'docker buildx rm "$BUILDER"\' EXIT'
        self.assertIn(cleanup, script)
        self.assertLess(script.index(cleanup), script.index("docker buildx build"))


if __name__ == "__main__":
    unittest.main()

# test_docker_publish_registry_secret.py
"""
Functional regression tests for Docker publishing registry configuration.
Version: 0.261.028
Implemented in: 0.261.028

Validate matching login/build secrets, fail-fast behavior, and image commands.
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
        self.assertLess(script.index(guard), script.index("docker build"))

    def test_both_tags_are_built_and_pushed(self):
        script = self.build["run"]
        self.assertIn('IMAGE="${ACR_LOGIN_SERVER}/simple-chat"', script)
        self.assertIn(
            'TAG="$(date +\'%Y-%m-%d\')_${BRANCH_TAG}_${GITHUB_RUN_NUMBER}"',
            script,
        )
        commands = [line for line in script.splitlines() if line.startswith("docker ")]
        self.assertEqual(commands, [
            'docker build . --file application/single_app/Dockerfile --tag "${IMAGE}:${TAG}"',
            'docker tag "${IMAGE}:${TAG}" "${IMAGE}:latest"',
            'docker push "${IMAGE}:${TAG}"',
            'docker push "${IMAGE}:latest"',
        ])


if __name__ == "__main__":
    unittest.main()

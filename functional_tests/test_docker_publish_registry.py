# test_docker_publish_registry.py
"""
Functional test for Docker publish registry consistency.
Version: 0.261.028
Implemented in: 0.261.028

Ensure every main image reference uses the registry secret used for login.
"""

from pathlib import Path
import re
import unittest


class DockerPublishRegistryTests(unittest.TestCase):
    def test_image_references_use_login_registry(self):
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/docker_image_publish.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")
        login_registry = re.search(
            r"login-server:\s*(\$\{\{\s*secrets\.\w+\s*\}\})", workflow
        )
        self.assertIsNotNone(login_registry)
        self.assertEqual(
            login_registry.group(1), "${{ secrets.MAIN_ACR_LOGIN_SERVER }}"
        )

        image_registries = re.findall(
            r"(\$\{\{\s*secrets\.\w+\s*\}\})/simple-chat:", workflow
        )
        self.assertEqual(len(image_registries), 5)
        for registry in image_registries:
            self.assertEqual(registry, login_registry.group(1))


if __name__ == "__main__":
    unittest.main()

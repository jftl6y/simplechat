# test_cryptography_snowflake_dependency_compatibility.py
"""
Regression test for the commercial mail and Snowflake dependency conflict.
Version: 0.261.034
Implemented in: 0.261.034

Checks the installed, manifest-pinned packages agree on the installed CFFI
version without importing the application or connecting to external services.
"""

from importlib.metadata import distribution, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


REQUIREMENTS = Path(__file__).resolve().parents[1] / "application" / "single_app" / "requirements.txt"


def test_commercial_mail_and_snowflake_share_compatible_cffi():
    manifest = [
        Requirement(line.strip())
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    installed_cffi = Version(version("cffi"))
    for package in ("cryptography", "snowflake-connector-python"):
        declared = next(item for item in manifest if item.name.lower() == package)
        installed = distribution(package)
        assert Version(installed.version) in declared.specifier
        cffi_requirements = [
            item for item in map(Requirement, installed.requires or [])
            if item.name.lower() == "cffi" and (item.marker is None or item.marker.evaluate())
        ]
        assert cffi_requirements, f"{package} has no applicable CFFI dependency"
        assert all(installed_cffi in item.specifier for item in cffi_requirements), (
            f"{package} does not accept installed CFFI {installed_cffi}"
        )

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _workflow() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())


def test_ci_covers_supported_python_versions() -> None:
    workflow = _workflow()
    versions = workflow["jobs"]["pytest"]["strategy"]["matrix"]["python-version"]

    assert versions == ["3.11", "3.12", "3.13"]


def test_ci_builds_and_audits_release_wheel() -> None:
    workflow_text = (ROOT / ".github/workflows/tests.yml").read_text()

    assert "python -m pip wheel --no-deps --wheel-dir dist ." in workflow_text
    assert 'assert "homelab_console/console.tcss" in names' in workflow_text
    assert 'assert not any(name.startswith("tests/")' in workflow_text

from pathlib import Path
import hashlib
import tomllib

import homelab_console


ROOT = Path(__file__).resolve().parents[1]


def test_project_metadata_is_release_ready_and_version_is_synced() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = data["project"]

    assert project["name"] == "touchrack"
    assert project["version"] == homelab_console.__version__
    assert project["version"] == "4.7.0"
    assert "> Current release: **v4.7.0**." in (ROOT / "README.md").read_text()
    assert project["readme"] == "README.md"
    assert project["license"] == "MIT"
    assert "Environment :: Console" in project["classifiers"]
    assert "Operating System :: POSIX :: Linux" in project["classifiers"]
    assert "Programming Language :: Python :: 3.13" in project["classifiers"]
    assert {"homelab", "tui", "linux", "tty", "textual", "touchscreen"} <= set(
        project["keywords"]
    )


def test_public_cli_name_remains_compatible() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert data["project"]["scripts"]["homelab-console"] == "homelab_console.app:main"


def test_sensitive_core_hash_manifest_matches_current_sources() -> None:
    manifest = (ROOT / "CORE-LOGIC-SHA256.txt").read_text().splitlines()

    for line in manifest:
        expected, relative = line.split(maxsplit=1)
        path = ROOT / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected, relative


def test_local_verify_artifacts_are_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text()

    assert ".verify-*.service" in gitignore
    assert "*.audit.txt" in gitignore



def test_changelog_contains_current_release() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text()

    assert "## [4.7.0] - 2026-08-17" in changelog
    assert "9.6 UNSAFE" in changelog
    assert "4.2 OK" in changelog
    assert "homelab-console" in changelog

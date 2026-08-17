from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_current_primary_screens() -> None:
    readme = (ROOT / "README.md").read_text()

    assert "**Storage**" in readme
    assert "up to 12 services per screen" in readme
    assert "Host · Services · Storage · Containers · Diagnostics" in readme
    assert "up to six curated checks" not in readme


def test_dependency_doc_matches_declared_python_runtime() -> None:
    doc = (ROOT / "docs/DEPENDENCIES.md").read_text()
    pyproject = (ROOT / "pyproject.toml").read_text()

    for package in ("textual", "psutil", "evdev", "PyYAML"):
        assert package in pyproject
        assert package in doc

    assert "pytest" in doc
    assert "pytest-asyncio" in doc


def test_dependency_doc_separates_host_tools_correctly() -> None:
    doc = (ROOT / "docs/DEPENDENCIES.md").read_text()

    assert "`openvt` | `kbd`" in doc
    assert "`setfont` | `kbd`" in doc
    assert "`setterm` | `util-linux`" in doc
    assert "`Uni3-Terminus32x16.psf.gz` | `console-setup-linux`" in doc
    assert "does **not** require a system-wide `python3-pip`" in doc
    assert "smartmontools" in doc
    assert "podman" in doc
    assert "docker.io" in doc



def test_standalone_architecture_documents_storage_and_smart() -> None:
    architecture = (ROOT / "docs/architecture.mmd").read_text()

    assert "Host · Services · Storage · Containers · Diagnostics" in architecture
    assert 'STORE["StorageProvider"]' in architecture
    assert 'SMART["DiskHealthProvider"]' in architecture
    assert 'smartctl (optional)' in architecture

from pathlib import Path

from homelab_console.config import load_config
from homelab_console.services import ServicesRegistry


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


def test_remote_smart_doc_keeps_privilege_boundary_narrow() -> None:
    doc = (ROOT / "docs/REMOTE_SMART.md").read_text()
    helper = (ROOT / "scripts/touchrack-smartctl").read_text()

    assert (
        "NOPASSWD: /usr/local/libexec/touchrack-smartctl /dev/sda" in doc
    )
    assert "/etc/touchrack/allowed-disks" in doc
    assert 'if [ "$#" -ne 1 ]' in helper
    assert "grep -Fx" in helper
    assert "TOUCHRACK_SMARTCTL" not in helper


def test_remote_containers_doc_keeps_privilege_boundary_narrow() -> None:
    doc = (ROOT / "docs/REMOTE_CONTAINERS.md").read_text()
    helper = (ROOT / "scripts/touchrack-container-inventory").read_text()

    assert "touchrack-container-inventory \"\"" in doc
    assert 'if [ "$#" -ne 0 ]' in helper
    assert "touchrack_containers_protocol=1" in helper
    assert "podman ps --all --format json" in helper
    assert "docker ps --all --no-trunc" in helper
    assert "TOUCHRACK_" not in helper
    for operation in (" start ", " stop ", " restart ", " exec ", " run "):
        assert operation not in helper


def test_remote_node_docs_and_examples_are_linked_and_parseable() -> None:
    readme = (ROOT / "README.md").read_text()
    guide = (ROOT / "docs/REMOTE_NODES.md").read_text()
    config = load_config(ROOT / "config.multi-node.example.yaml")
    services = ServicesRegistry(ROOT / "services.multi-node.example.yaml").load()

    assert "docs/REMOTE_NODES.md" in readme
    assert "config.multi-node.example.yaml" in readme
    assert "services.multi-node.example.yaml" in guide
    assert config.nodes is not None
    assert [node.node.id for node in config.nodes] == [
        "touchrack-host",
        "remote-sbc",
    ]
    assert config.nodes[1].disk_health is not None
    assert config.nodes[1].disk_health.devices == ("/dev/sda",)
    assert config.nodes[1].containers is not None
    assert [service.provider for service in services] == [
        "tcp",
        "crux",
        "crux",
        "crux",
        "crux",
    ]
    assert all(service.node_id == "remote-sbc" for service in services)


def test_public_examples_do_not_expose_private_installation_details() -> None:
    public_files = (
        ROOT / "README.md",
        ROOT / "config.example.yaml",
        ROOT / "config.multi-node.example.yaml",
        ROOT / "services.example.yaml",
        ROOT / "services.multi-node.example.yaml",
        *(ROOT / "docs").glob("*.md"),
    )
    forbidden = (
        "192.168.1.",
        "lokalix",
        "rpi3",
        "odroid",
        "orange pi",
        "orangepi",
    )

    for path in public_files:
        text = path.read_text().casefold()
        for value in forbidden:
            assert value not in text, f"{value!r} leaked into {path.name}"

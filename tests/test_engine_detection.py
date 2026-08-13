from homelab_console.providers.containers import (
    AutoContainersProvider,
    DockerContainersProvider,
    PodmanContainersProvider,
)


def test_auto_provider_prefers_podman(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: f"/usr/bin/{command}")
    provider = AutoContainersProvider()
    assert isinstance(provider.provider, PodmanContainersProvider)


def test_auto_provider_uses_docker_when_podman_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda command: "/usr/bin/docker" if command == "docker" else None
    )
    provider = AutoContainersProvider()
    assert isinstance(provider.provider, DockerContainersProvider)


def test_auto_provider_reports_no_engine(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: None)
    provider = AutoContainersProvider()
    assert provider.provider is None

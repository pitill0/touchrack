from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timezone

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.models import (
    ContainersSnapshot,
    HostSnapshot,
    StorageSnapshot,
)
from homelab_console.nodes import Node, NodeRegistry, NodeRuntime
from homelab_console.providers import (
    UnavailableContainersProvider,
    UnavailableDiskHealthProvider,
    UnavailableStorageProvider,
)
from homelab_console.screens import (
    ContainersView,
    HostView,
    ServicesView,
    StorageView,
)
from homelab_console.transports import CommandResult


def host_snapshot(hostname: str) -> HostSnapshot:
    return HostSnapshot(
        hostname=hostname,
        uptime_seconds=3600,
        cpu_percent=12.0,
        load_average=(0.1, 0.2, 0.3),
        memory_used=1,
        memory_total=2,
        disk_used=3,
        disk_total=4,
        temperature_c=40.0,
        ip_address="192.168.1.252",
        collected_at=datetime.now(timezone.utc),
    )


class HostProvider:
    def __init__(self, hostname: str) -> None:
        self.hostname = hostname
        self.calls = 0

    async def collect(self) -> HostSnapshot:
        self.calls += 1
        return host_snapshot(self.hostname)


class CancellationResistantHostProvider(HostProvider):
    def __init__(self, hostname: str) -> None:
        super().__init__(hostname)
        self.started = asyncio.Event()

    async def collect(self) -> HostSnapshot:
        self.calls += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Model a provider boundary that completes despite worker cancellation.
            return host_snapshot(self.hostname)
        raise AssertionError("unreachable")


class FlakyHostProvider(HostProvider):
    def __init__(self, hostname: str) -> None:
        super().__init__(hostname)
        self.error: str | None = None

    async def collect(self) -> HostSnapshot:
        self.calls += 1
        if self.error is None:
            return host_snapshot(self.hostname)
        return HostSnapshot(
            hostname=self.hostname,
            uptime_seconds=0,
            cpu_percent=0,
            load_average=None,
            memory_used=0,
            memory_total=0,
            disk_used=0,
            disk_total=0,
            temperature_c=None,
            ip_address=None,
            collected_at=datetime.now(timezone.utc),
            error=self.error,
        )


def runtime(
    node_id: str,
    title: str,
    transport: str,
    provider: HostProvider,
    command_runner=None,
) -> NodeRuntime:
    reason = "Remote capability is not available yet"
    return NodeRuntime(
        node=Node(id=node_id, title=title, transport=transport),
        host=provider,
        containers=UnavailableContainersProvider(reason),
        storage=UnavailableStorageProvider(reason),
        disk_health=UnavailableDiskHealthProvider(reason),
        command_runner=command_runner,
    )


def multi_node_app(
    local_provider: HostProvider | None = None,
) -> tuple[HomelabConsole, HostProvider, HostProvider]:
    local = local_provider or HostProvider("lokalix")
    remote = HostProvider("rpi3")
    registry = NodeRegistry(
        (
            runtime("lokalix", "Lokalix", "local", local),
            runtime("rpi3", "Raspberry Pi 3", "ssh", remote),
        ),
        default_node_id="lokalix",
    )
    return (
        HomelabConsole(
            nodes=registry,
            refresh_seconds=3600,
            screen_blank_minutes=0,
            touch_enabled=False,
        ),
        local,
        remote,
    )


@pytest.mark.asyncio
async def test_touch_selector_switches_node_at_64x18() -> None:
    app, _, remote = multi_node_app()

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        selector = app.query_one("#select-node")
        assert str(selector.label) == "LOKALIX"
        assert app.screen.region.contains_region(selector.region)

        await pilot.click("#select-node")
        await pilot.pause()
        assert app.screen.query_one("#node-option-lokalix").disabled
        assert not app.screen.query_one("#node-option-rpi3").disabled
        assert str(app.screen.query_one("#node-option-lokalix").label).endswith(
            " · UP"
        )
        assert str(app.screen.query_one("#node-option-rpi3").label).endswith(
            " · --"
        )

        await pilot.click("#node-option-rpi3")
        await pilot.pause()
        await pilot.pause()

        assert app.active_node_id == "rpi3"
        assert app.active_section == "host"
        assert str(app.query_one("#select-node").label) == "RPI3"
        assert app.query_one(HostView).snapshot.hostname == "rpi3"
        assert app.node_connectivity.get("rpi3").status == "online"
        assert remote.calls == 1
        services = app.query_one(ServicesView).snapshot
        assert services is not None
        assert services.error is None
        assert services.services == ()


@pytest.mark.asyncio
async def test_touch_selector_pages_through_seven_nodes_at_64x18() -> None:
    runtimes = tuple(
        runtime(
            f"node-{index}",
            f"Node {index}",
            "local" if index == 0 else "ssh",
            HostProvider(f"node-{index}"),
        )
        for index in range(7)
    )
    app = HomelabConsole(
        nodes=NodeRegistry(runtimes, default_node_id="node-0"),
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.click("#select-node")
        await pilot.pause()

        assert str(app.screen.query_one("#node-selector-page").content) == "1 / 3"
        assert app.screen.query_one("#node-selector-prev").disabled
        assert not app.screen.query_one("#node-selector-next").disabled
        assert app.screen.query_one("#node-option-node-0").display
        assert app.screen.query_one("#node-option-node-2").display
        assert not app.screen.query_one("#node-option-node-3").display
        for selector in (
            "#node-selector-prev",
            "#node-selector-next",
            "#node-selector-cancel",
        ):
            assert app.screen.region.contains_region(
                app.screen.query_one(selector).region
            )

        await pilot.click("#node-selector-next")
        await pilot.pause()
        await pilot.pause(0.6)
        assert str(app.screen.query_one("#node-selector-page").content) == "2 / 3"
        assert app.screen.query_one("#node-option-node-3").display
        assert app.screen.query_one("#node-option-node-5").display

        await pilot.click("#node-selector-next")
        await pilot.pause()
        await pilot.pause()
        assert str(app.screen.query_one("#node-selector-page").content) == "3 / 3"
        assert app.screen.query_one("#node-selector-next").disabled
        assert app.screen.query_one("#node-option-node-6").display

        await pilot.click("#node-option-node-6")
        await pilot.pause()
        await pilot.pause()

        assert app.active_node_id == "node-6"
        assert app.query_one(HostView).snapshot.hostname == "node-6"


@pytest.mark.asyncio
async def test_single_node_keeps_original_four_way_navigation() -> None:
    provider = HostProvider("lokalix")
    registry = NodeRegistry(
        (runtime("lokalix", "Lokalix", "local", provider),),
        default_node_id="lokalix",
    )
    app = HomelabConsole(
        nodes=registry,
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        assert len(app.query("#navigation .nav")) == 4
        assert not app.query("#select-node")


@pytest.mark.asyncio
async def test_late_previous_node_snapshot_cannot_replace_active_node() -> None:
    slow_local = CancellationResistantHostProvider("stale-lokalix")
    app, _, _ = multi_node_app(slow_local)

    async with app.run_test(size=(64, 18)) as pilot:
        await slow_local.started.wait()
        app.select_node("rpi3")
        await pilot.pause()
        await pilot.pause()

        assert app.active_node_id == "rpi3"
        assert app.query_one(HostView).snapshot.hostname == "rpi3"


@pytest.mark.asyncio
async def test_failed_refresh_keeps_last_host_snapshot_marked_stale() -> None:
    remote = FlakyHostProvider("rpi3")
    registry = NodeRegistry(
        (
            runtime("lokalix", "Lokalix", "local", HostProvider("lokalix")),
            runtime("rpi3", "Raspberry Pi 3", "ssh", remote),
        ),
        default_node_id="lokalix",
    )
    app = HomelabConsole(
        nodes=registry,
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        app.select_node("rpi3")
        await pilot.pause()
        await pilot.pause()
        first = app.query_one(HostView).snapshot
        assert first is not None
        assert first.cpu_percent == 12.0

        remote.error = "SSH timed out"
        app.refresh_host()
        await pilot.pause()
        await pilot.pause()

        stale = app.query_one(HostView).snapshot
        assert stale is not None
        assert stale.cpu_percent == 12.0
        assert stale.collected_at == first.collected_at
        assert stale.stale is True
        assert stale.stale_error == "SSH timed out"
        assert str(app.query_one("#host-health").content) == "● STALE"
        assert str(app.query_one("#host-uptime").content) == "STALE DATA"
        assert app.node_connectivity.get("rpi3").status == "offline"


@pytest.mark.asyncio
async def test_remote_node_uses_scoped_systemd_service_over_its_runner(
    tmp_path,
) -> None:
    services_path = tmp_path / "services.yaml"
    services_path.write_text(
        """
services:
  - id: rpi3-sshd
    title: RPi3 SSH
    node: rpi3
    provider: systemd
    target: sshd
""".strip(),
        encoding="utf-8",
    )

    class Runner:
        def __init__(self) -> None:
            self.commands = []

        async def run(self, command, *, timeout=None):
            self.commands.append(command)
            return CommandResult(command, "active\n", "", 0)

    runner = Runner()
    registry = NodeRegistry(
        (
            runtime("lokalix", "Lokalix", "local", HostProvider("lokalix")),
            runtime(
                "rpi3",
                "Raspberry Pi 3",
                "ssh",
                HostProvider("rpi3"),
                command_runner=runner,
            ),
        ),
        default_node_id="lokalix",
    )
    app = HomelabConsole(
        nodes=registry,
        services_config_path=str(services_path),
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        app.select_node("rpi3")
        await pilot.pause()
        await pilot.pause()

        snapshot = app.query_one(ServicesView).snapshot
        assert snapshot is not None
        assert [(state.id, state.status) for state in snapshot.services] == [
            ("rpi3-sshd", "OK")
        ]
        assert ("systemctl", "is-active", "sshd") in runner.commands


@pytest.mark.asyncio
async def test_remote_node_uses_scoped_crux_service_over_its_runner(
    tmp_path,
) -> None:
    services_path = tmp_path / "services.yaml"
    services_path.write_text(
        """
services:
  - id: rpi3-sshd
    title: RPi3 SSH
    node: rpi3
    provider: crux
    target: sshd
""".strip(),
        encoding="utf-8",
    )

    class Runner:
        def __init__(self) -> None:
            self.commands = []

        async def run(self, command, *, timeout=None):
            self.commands.append(command)
            return CommandResult(command, "sshd is running\n", "", 0)

    runner = Runner()
    registry = NodeRegistry(
        (
            runtime("lokalix", "Lokalix", "local", HostProvider("lokalix")),
            runtime(
                "rpi3",
                "Raspberry Pi 3",
                "ssh",
                HostProvider("rpi3"),
                command_runner=runner,
            ),
        ),
        default_node_id="lokalix",
    )
    app = HomelabConsole(
        nodes=registry,
        services_config_path=str(services_path),
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        app.select_node("rpi3")
        await pilot.pause()
        await pilot.pause()

        snapshot = app.query_one(ServicesView).snapshot
        assert snapshot is not None
        assert [(state.id, state.status) for state in snapshot.services] == [
            ("rpi3-sshd", "OK")
        ]
        assert ("/etc/rc.d/sshd", "status") in runner.commands



@pytest.mark.asyncio
async def test_late_previous_node_host_exception_cannot_pollute_active_node():
    """A cancelled provider that raises must not affect the newly active node."""

    class LateFailureHostProvider(HostProvider):
        def __init__(self):
            super().__init__("lokalix")
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def collect(self):
            self.calls += 1
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise RuntimeError("late previous-node host exception") from None

    old_provider = LateFailureHostProvider()
    app, _, _ = multi_node_app(old_provider)

    async with app.run_test(size=(64, 18)) as pilot:
        await asyncio.wait_for(old_provider.started.wait(), timeout=5)

        app.select_node("rpi3")

        await pilot.pause()
        await pilot.pause()

        assert app.active_node_id == "rpi3"
        assert app.is_running

        # The replaced worker is expected to be cancelled. Its exception
        # cannot update the active node's connectivity or Host presentation.
        assert old_provider.cancelled.is_set()

        view = app.query_one(HostView)
        assert view.snapshot is not None
        assert view.snapshot.hostname == "rpi3"
        assert str(app.query_one("#host-health").content) == "● OK"

        current = app.node_connectivity.get("rpi3")
        assert current.status == "online"
        assert current.error is None

        previous = app.node_connectivity.get("lokalix")
        assert previous.status == "unknown"
        assert previous.error is None



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "view_type", "status_selector"),
    [
        ("containers", ContainersView, "#containers-status"),
        ("storage", StorageView, "#storage-status"),
    ],
)
async def test_late_capability_exception_cannot_pollute_new_node(
    capability, view_type, status_selector
):
    """Cancelled old-node collection must never publish to a new node."""

    class LateFailureProvider:
        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def collect(self):
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise RuntimeError(
                    f"old-node-{capability}-failure"
                ) from None

    delayed = LateFailureProvider()
    local = replace(
        runtime("lokalix", "Lokalix", "local", HostProvider("lokalix")),
        **{capability: delayed},
    )
    remote = runtime("rpi3", "Raspberry Pi 3", "ssh", HostProvider("rpi3"))
    app = HomelabConsole(
        nodes=NodeRegistry(
            (local, remote),
            default_node_id="lokalix",
        ),
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await asyncio.wait_for(delayed.started.wait(), timeout=5)

        app.select_node("rpi3")
        await pilot.pause()
        await pilot.pause()

        assert delayed.cancelled.is_set()
        assert app.active_node_id == "rpi3"
        assert app.is_running

        view = app.query_one(view_type)
        assert view.snapshot is not None
        assert view.snapshot.error == (
            "Remote capability is not available yet"
        )

        status = str(app.query_one(status_selector).content)
        assert f"old-node-{capability}-failure" not in status

        assert app.query_one(HostView).snapshot.hostname == "rpi3"
        assert app.node_connectivity.get("rpi3").status == "online"

        if capability == "containers":
            assert "lokalix" not in app._container_snapshots
            assert "rpi3" in app._container_snapshots



@pytest.mark.asyncio
async def test_late_previous_node_services_exception_cannot_pollute_active_node():
    """A cancelled old-node Services worker must not affect the new node."""
    app, _, _ = multi_node_app()

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert app.active_node_id == "lokalix"
        old_manager = app._services_manager(app.active_node)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def late_failure():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise RuntimeError("old-node-services-failure") from None

        # Inject the failure only at the Services collection boundary,
        # leaving the per-node manager registration intact.
        old_manager.collect = late_failure
        old_worker = app.refresh_services()
        await asyncio.wait_for(started.wait(), timeout=5)

        app.select_node("rpi3")

        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert old_worker.is_cancelled or cancelled.is_set()
        assert cancelled.is_set()
        assert app.active_node_id == "rpi3"
        assert app.is_running

        view = app.query_one(ServicesView)
        assert view.snapshot is not None
        assert view.snapshot.error is None
        assert view.snapshot.services == ()

        status = str(app.query_one("#services-status").content)
        health = str(app.query_one("#services-health").content)

        assert "old-node-services-failure" not in status
        assert "STALE" not in health
        assert "DOWN" not in health

        assert app.query_one(HostView).snapshot.hostname == "rpi3"
        assert app.node_connectivity.get("rpi3").status == "online"



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "view_type", "health_selector"),
    [
        ("containers", ContainersView, "#containers-health"),
        ("storage", StorageView, "#storage-health"),
    ],
)
async def test_node_transition_failure_does_not_retain_previous_node_data(
    capability, view_type, health_selector
):
    """A failed node B refresh cannot display the last snapshot of node A."""

    class FixedProvider:
        def __init__(self, snapshot):
            self.snapshot = snapshot

        async def collect(self):
            return self.snapshot

    class FailingProvider:
        async def collect(self):
            raise RuntimeError("node-b-collection-failure")

    now = datetime.now(UTC)
    snapshots = {
        "containers": ContainersSnapshot(
            containers=(),
            collected_at=now,
            engine="node-a-engine",
        ),
        "storage": StorageSnapshot(
            filesystems=(),
            collected_at=now,
        ),
    }

    local = replace(
        runtime("lokalix", "Lokalix", "local", HostProvider("lokalix")),
        **{capability: FixedProvider(snapshots[capability])},
    )
    remote = replace(
        runtime("rpi3", "Raspberry Pi 3", "ssh", HostProvider("rpi3")),
        **{capability: FailingProvider()},
    )

    app = HomelabConsole(
        nodes=NodeRegistry(
            (local, remote),
            default_node_id="lokalix",
        ),
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        view = app.query_one(view_type)
        previous = view.snapshot

        assert previous is not None
        assert previous.error is None

        app.select_node("rpi3")

        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert app.active_node_id == "rpi3"
        assert app.is_running
        assert app.query_one(HostView).snapshot.hostname == "rpi3"

        # A snapshot from lokalix must not become STALE telemetry for rpi3.
        assert view.snapshot is None
        assert str(app.query_one(health_selector).content) == "● DOWN"



@pytest.mark.asyncio
async def test_node_switch_clears_previous_node_touch_references():
    """Touch targets must never open previous-node details after switching."""
    app, _, _ = multi_node_app()

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()

        containers = app.query_one(ContainersView)
        storage = app.query_one(StorageView)
        services = app.query_one(ServicesView)

        # Inject distinct old-node references into each detail lookup.
        # Their identity matters; the model's payload is irrelevant here.
        old_container = object()
        old_filesystem = object()
        old_disk = object()
        old_service = object()

        containers.displayed_containers = (old_container,)
        storage._visible_filesystems = (old_filesystem,)
        storage._visible_disks = (old_disk,)
        services._page_states = (old_service,)

        assert containers.container_for_button("container-open-0") is old_container
        assert storage.filesystem_for_button("storage-open-0") is old_filesystem
        assert storage.disk_for_button("storage-disk-open-0") is old_disk
        assert services.state_for_button("service-open-0") is old_service

        app.select_node("rpi3")

        # Check synchronously: no worker result should be needed to clear
        # old-node interactive references.
        assert app.active_node_id == "rpi3"
        assert containers.snapshot is None
        assert storage.snapshot is None
        assert storage.disk_snapshot is None
        assert services.snapshot is None

        assert containers.container_for_button("container-open-0") is None
        assert storage.filesystem_for_button("storage-open-0") is None
        assert storage.disk_for_button("storage-disk-open-0") is None
        assert services.state_for_button("service-open-0") is None

        assert containers.displayed_containers == ()
        assert storage._visible_filesystems == ()
        assert storage._visible_disks == ()
        assert services._page_states == ()

        assert str(app.query_one("#containers-health").content) == "● --"
        assert str(app.query_one("#storage-health").content) == "● --"
        assert str(app.query_one("#services-health").content) == "● --"

        await pilot.pause()
        await pilot.pause()
        assert app.is_running

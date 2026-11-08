"""Regression coverage for cooperative TouchRack shutdown."""

from __future__ import annotations

import asyncio
import threading

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.nodes import Node, NodeRegistry, NodeRuntime
from homelab_console.providers.disk_health import SmartctlDiskHealthProvider
from homelab_console.providers.local import LocalHostProvider
from homelab_console.providers.remote_disk_health import (
    RemoteSmartctlDiskHealthProvider,
)
from homelab_console.providers.storage import LocalStorageProvider
from homelab_console.services import (
    HttpServiceProvider,
    ServiceDefinition,
    ServicesManager,
    TcpServiceProvider,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,provider_class",
    [
        ("http", HttpServiceProvider),
        ("tcp", TcpServiceProvider),
    ],
)
async def test_cancelled_caller_does_not_abandon_thread(
    kind, provider_class, monkeypatch
):
    started = threading.Event()
    release = threading.Event()
    exited = threading.Event()

    provider = provider_class()
    definition = ServiceDefinition(
        id="owned",
        title="Owned",
        provider=kind,
        target=(
            "http://example.invalid/" if kind == "http" else "example.invalid:12345"
        ),
    )

    def controlled_check(_definition):
        started.set()
        try:
            assert release.wait(timeout=8), "Thread release timed out"
            raise RuntimeError("controlled late failure")
        finally:
            exited.set()

    monkeypatch.setattr(provider, "_check_sync", controlled_check)

    loop = asyncio.get_running_loop()
    contexts = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))

    caller = asyncio.create_task(provider.check(definition))

    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 3), timeout=4)

        assert len(provider._thread_checks) == 1

        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)

        assert caller.cancelled()
        assert len(provider._thread_checks) == 1

        shutdown = asyncio.create_task(provider.shutdown())
        done, pending = await asyncio.wait((shutdown,), timeout=0.1)

        assert not done and len(pending) == 1
        assert not exited.is_set()

        release.set()
        await asyncio.wait_for(shutdown, timeout=4)

        assert exited.is_set()
        assert not provider._thread_checks
        assert not contexts

        print(
            f"{kind.upper()}_OWNERSHIP:",
            "caller_cancelled=",
            caller.cancelled(),
            "thread_exited=",
            exited.is_set(),
            "tracked=",
            len(provider._thread_checks),
            "loop_errors=",
            len(contexts),
            flush=True,
        )
    finally:
        release.set()
        try:
            await asyncio.gather(caller, return_exceptions=True)
            await asyncio.wait_for(provider.shutdown(), timeout=5)
        finally:
            loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_two_managers_drain_shared_provider(monkeypatch):
    started = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    exited = [threading.Event(), threading.Event()]
    calls = [0, 0]

    provider = HttpServiceProvider()

    def controlled_check(definition):
        index = int(definition.id)
        calls[index] += 1
        started[index].set()

        try:
            assert release[index].wait(timeout=8)
        finally:
            exited[index].set()

    monkeypatch.setattr(provider, "_check_sync", controlled_check)

    class Registry:
        path = None

        def __init__(self, index):
            self.index = index

        def load(self):
            return (
                ServiceDefinition(
                    id=str(self.index),
                    title=f"Shared {self.index}",
                    provider="http",
                    target="http://example.invalid/",
                ),
            )

    managers = [
        ServicesManager(Registry(index), {"http": provider}) for index in range(2)
    ]
    callers = [asyncio.create_task(manager.collect()) for manager in managers]
    shutdowns = []

    try:
        for event in started:
            assert await asyncio.wait_for(asyncio.to_thread(event.wait, 3), timeout=4)

        assert len(provider._thread_checks) == 2

        shutdowns = [asyncio.create_task(manager.shutdown()) for manager in managers]

        done, pending = await asyncio.wait(shutdowns, timeout=0.1)
        assert not done and len(pending) == 2

        release[0].set()
        assert await asyncio.wait_for(asyncio.to_thread(exited[0].wait, 3), timeout=4)

        done, pending = await asyncio.wait(shutdowns, timeout=0.1)
        assert not done and len(pending) == 2

        release[1].set()

        await asyncio.wait_for(asyncio.gather(*shutdowns), timeout=4)
        await asyncio.gather(*callers, return_exceptions=True)

        assert all(event.is_set() for event in exited)
        assert calls == [1, 1]
        assert not provider._thread_checks

        print(
            "SHARED_MANAGER_SHUTDOWN:",
            "shutdowns=",
            len(shutdowns),
            "threads_exited=",
            sum(e.is_set() for e in exited),
            "tracked=",
            len(provider._thread_checks),
            flush=True,
        )
    finally:
        for event in release:
            event.set()

        await asyncio.wait_for(
            asyncio.gather(
                *shutdowns,
                *callers,
                return_exceptions=True,
            ),
            timeout=5,
        )
        await asyncio.wait_for(provider.shutdown(), timeout=5)


@pytest.mark.asyncio
async def test_app_unmount_drains_inactive_nodes_and_shared_providers(
    monkeypatch,
):
    shared_host = LocalHostProvider()
    shared_storage = LocalStorageProvider()
    local_smart = SmartctlDiskHealthProvider()
    remote_smart = RemoteSmartctlDiskHealthProvider(
        runner=object(),
        devices=(),
        helper="/unused/helper",
    )

    nodes = NodeRegistry(
        (
            NodeRuntime(
                node=Node(id="local", title="Local"),
                host=shared_host,
                storage=shared_storage,
                containers=object(),
                disk_health=local_smart,
            ),
            NodeRuntime(
                node=Node(
                    id="remote",
                    title="Remote",
                    transport="ssh",
                ),
                host=shared_host,
                storage=shared_storage,
                containers=object(),
                disk_health=remote_smart,
            ),
        ),
        default_node_id="local",
    )

    app = HomelabConsole(nodes=nodes, touch_enabled=False)

    class Manager:
        pass

    local_manager = Manager()
    remote_manager = Manager()

    app._services_managers = {
        "local": local_manager,
        "remote": remote_manager,
    }

    names = (
        "manager-local",
        "manager-remote",
        "host-shared",
        "storage-shared",
        "smart-local",
        "smart-remote",
    )
    resources = (
        local_manager,
        remote_manager,
        shared_host,
        shared_storage,
        local_smart,
        remote_smart,
    )

    reached = set()
    calls = {name: 0 for name in names}
    release = asyncio.Event()

    def build_shutdown(name):
        async def shutdown():
            calls[name] += 1
            reached.add(name)
            await release.wait()

        return shutdown

    for name, resource in zip(names, resources):
        monkeypatch.setattr(
            resource,
            "shutdown",
            build_shutdown(name),
            raising=False,
        )

    closing = asyncio.create_task(app.on_unmount())

    try:
        for _ in range(200):
            if reached == set(names):
                break
            await asyncio.sleep(0.005)

        assert reached == set(names)
        assert not closing.done()

        assert all(count == 1 for count in calls.values())

        print(
            "TEXTUAL_ALL_NODES_WAITING:",
            "resources=",
            len(reached),
            "closing_done=",
            closing.done(),
            "calls=",
            calls,
            flush=True,
        )

        release.set()
        await asyncio.wait_for(closing, timeout=4)

        assert all(count == 1 for count in calls.values())

        print(
            "TEXTUAL_ALL_NODES_DRAINED:",
            "closing_done=",
            closing.done(),
            "resources=",
            len(reached),
            flush=True,
        )
    finally:
        release.set()
        await asyncio.wait_for(
            asyncio.gather(closing, return_exceptions=True),
            timeout=5,
        )


@pytest.mark.asyncio
async def test_unmount_waits_for_other_resources_when_manager_fails():
    """One failing manager must not shorten other resource shutdowns."""
    host = LocalHostProvider()
    storage = LocalStorageProvider()

    nodes = NodeRegistry(
        (
            NodeRuntime(
                node=Node(id="local", title="Local"),
                host=host,
                storage=storage,
                containers=object(),
                disk_health=object(),
            ),
        ),
        default_node_id="local",
    )
    app = HomelabConsole(nodes=nodes, touch_enabled=False)

    started = set()
    completed = set()
    release = asyncio.Event()
    both_started = asyncio.Event()
    drains = []

    async def drain(name):
        drains.append(asyncio.current_task())
        started.add(name)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        completed.add(name)

    async def host_shutdown():
        await drain("host")

    async def storage_shutdown():
        await drain("storage")

    expected_error = RuntimeError("controlled manager shutdown failure")

    class FailingManager:
        async def shutdown(self):
            await both_started.wait()
            raise expected_error

    host.shutdown = host_shutdown
    storage.shutdown = storage_shutdown
    app._services_managers = {"local": FailingManager()}

    closing = asyncio.create_task(app.on_unmount())

    try:
        await asyncio.wait_for(both_started.wait(), timeout=3)

        # The manager fails as soon as both other shutdowns are running.
        # Give the event loop time to propagate this exception.
        done, pending = await asyncio.wait((closing,), timeout=0.1)

        print(
            "PARTIAL_FAILURE_BEFORE_RELEASE:",
            "unmount_done=",
            bool(done),
            "drains_pending=",
            sum(not task.done() for task in drains),
            "completed=",
            sorted(completed),
            flush=True,
        )

        assert not done, "on_unmount returned before waiting for Host and Storage"
        assert len(pending) == 1
        assert not completed

        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(closing, return_exceptions=True),
            timeout=4,
        )

        assert completed == {"host", "storage"}
        assert results[0] is expected_error

        print(
            "PARTIAL_FAILURE_AFTER_RELEASE:",
            "unmount_done=",
            closing.done(),
            "completed=",
            sorted(completed),
            flush=True,
        )
    finally:
        release.set()
        await asyncio.wait_for(
            asyncio.gather(closing, return_exceptions=True),
            timeout=5,
        )
        await asyncio.wait_for(
            asyncio.gather(*drains, return_exceptions=True),
            timeout=5,
        )

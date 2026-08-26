from homelab_console.providers.base import (
    ContainersProvider,
    DiskHealthProvider,
    HostProvider,
    StorageProvider,
)
from homelab_console.providers.containers import (
    AutoContainersProvider,
    DockerContainersProvider,
    PodmanContainersProvider,
)
from homelab_console.providers.disk_health import SmartctlDiskHealthProvider
from homelab_console.providers.local import LocalHostProvider
from homelab_console.providers.remote import RemoteHostProvider
from homelab_console.providers.remote_containers import RemoteContainersProvider
from homelab_console.providers.remote_disk_health import RemoteSmartctlDiskHealthProvider
from homelab_console.providers.remote_storage import RemoteStorageProvider
from homelab_console.providers.storage import LocalStorageProvider
from homelab_console.providers.unavailable import (
    UnavailableContainersProvider,
    UnavailableDiskHealthProvider,
    UnavailableStorageProvider,
)

__all__ = [
    "AutoContainersProvider",
    "ContainersProvider",
    "DockerContainersProvider",
    "HostProvider",
    "LocalHostProvider",
    "RemoteHostProvider",
    "RemoteContainersProvider",
    "RemoteSmartctlDiskHealthProvider",
    "RemoteStorageProvider",
    "PodmanContainersProvider",
    "LocalStorageProvider",
    "StorageProvider",
    "DiskHealthProvider",
    "SmartctlDiskHealthProvider",
    "UnavailableContainersProvider",
    "UnavailableDiskHealthProvider",
    "UnavailableStorageProvider",
]

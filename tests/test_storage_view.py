from __future__ import annotations

from datetime import datetime, timezone

import pytest

from textual.app import App, ComposeResult

from homelab_console.models import (
    DiskHealthInfo,
    DiskHealthSnapshot,
    FilesystemInfo,
    StorageSnapshot,
)
from homelab_console.screens.storage import (
    DiskDetailScreen,
    StorageDetailScreen,
    StorageView,
    storage_severity,
)


class StorageViewApp(App[None]):
    CSS_PATH = "../src/homelab_console/console.tcss"

    def compose(self) -> ComposeResult:
        yield StorageView(id="view-storage")

    def on_mount(self) -> None:
        self.screen.add_class("compact-touch")


def _filesystem(
    mountpoint: str = "/",
    *,
    usage_percent: float = 40.0,
    device: str = "/dev/nvme0n1p2",
) -> FilesystemInfo:
    total = 1000
    used = round(total * usage_percent / 100)
    return FilesystemInfo(
        device=device,
        mountpoint=mountpoint,
        filesystem="ext4",
        mount_options="rw,noatime",
        total=total,
        used=used,
        free=total - used,
        usage_percent=usage_percent,
    )




def _disk(
    name: str = "nvme0n1",
    *,
    smart_passed: bool | None = True,
    temperature_c: float | None = 41,
) -> DiskHealthInfo:
    return DiskHealthInfo(
        device=f"/dev/{name}",
        name=name,
        model="Example Disk",
        serial="SER123",
        protocol="NVMe",
        size=1_000_000_000_000,
        temperature_c=temperature_c,
        smart_passed=smart_passed,
        power_on_hours=1000,
        power_cycles=50,
        percentage_used=5,
        details=(("WEAR USED", "5%"),),
    )

def test_storage_severity_thresholds() -> None:
    assert storage_severity(74.9) == "normal"
    assert storage_severity(75.0) == "warning"
    assert storage_severity(89.9) == "warning"
    assert storage_severity(90.0) == "critical"


@pytest.mark.asyncio
async def test_storage_view_renders_four_filesystems_at_physical_size() -> None:
    app = StorageViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(StorageView)
        filesystems = (
            _filesystem("/", usage_percent=40),
            _filesystem("/home", usage_percent=61, device="/dev/nvme0n1p3"),
            _filesystem("/data", usage_percent=82, device="/dev/sda1"),
            _filesystem("/backup", usage_percent=37, device="/dev/sdb1"),
            _filesystem("/fifth", usage_percent=10, device="/dev/sdc1"),
        )
        view.show_snapshot(
            StorageSnapshot(
                filesystems=filesystems,
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        assert str(app.query_one("#storage-health").content) == "● WARN"
        assert str(app.query_one("#storage-status").content) == ""

        for index in range(4):
            card = app.query_one(f"#storage-open-{index}")
            assert not card.disabled
            assert app.screen.region.contains_region(card.region)

        assert "40%" in str(app.query_one("#storage-open-0").label)
        assert "/data" in str(app.query_one("#storage-open-2").label)
        assert view.filesystem_for_button("storage-open-3") == filesystems[3]
        assert view.filesystem_for_button("storage-open-4") is None


@pytest.mark.asyncio
async def test_storage_healthy_status_row_is_blank() -> None:
    app = StorageViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(StorageView)
        view.show_snapshot(
            StorageSnapshot(
                filesystems=(_filesystem("/", usage_percent=20),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        assert str(app.query_one("#storage-health").content) == "● OK"
        assert str(app.query_one("#storage-status").content) == ""


@pytest.mark.asyncio
async def test_storage_detail_renders_useful_filesystem_information() -> None:
    filesystem = FilesystemInfo(
        device="/dev/sda1",
        mountpoint="/data",
        filesystem="xfs",
        mount_options="rw,noatime",
        total=2 * 1024**4,
        used=1640 * 1024**3,
        free=408 * 1024**3,
        usage_percent=80.1,
    )

    class DetailApp(App[None]):
        CSS_PATH = "../src/homelab_console/console.tcss"

        def on_mount(self) -> None:
            self.screen.add_class("compact-touch")
            self.push_screen(StorageDetailScreen(filesystem))

    app = DetailApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()

        assert isinstance(app.screen, StorageDetailScreen)
        lines = [
            str(widget.content)
            for widget in app.screen.query(".storage-detail-line")
        ]
        joined = "\n".join(lines)

        assert "DEVICE      /dev/sda1" in joined
        assert "FILESYSTEM  xfs" in joined
        assert "MOUNT       /data" in joined
        assert "USED" in joined
        assert "FREE" in joined
        assert "TOTAL" in joined
        assert "USAGE       80.1%" in joined
        assert "OPTIONS     rw,noatime" in joined

        back_button = app.screen.query_one("#storage-detail-back")
        assert app.screen.region.contains_region(back_button.region)



@pytest.mark.asyncio
async def test_storage_paginates_four_filesystems_per_page() -> None:
    app = StorageViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(StorageView)
        filesystems = tuple(
            _filesystem(
                f"/mnt/disk-{index}",
                usage_percent=10 + index,
                device=f"/dev/sd{chr(ord('a') + index)}1",
            )
            for index in range(5)
        )
        view.show_snapshot(
            StorageSnapshot(
                filesystems=filesystems,
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        pager = app.query_one("#storage-pager")
        assert not pager.has_class("pager-hidden")
        assert str(app.query_one("#storage-page-indicator").content) == "1 / 2"
        assert view.filesystem_for_button("storage-open-3") == filesystems[3]

        next_button = app.query_one("#storage-page-next")
        assert app.screen.region.contains_region(next_button.region)
        await pilot.click("#storage-page-next")
        await pilot.pause()

        assert str(app.query_one("#storage-page-indicator").content) == "2 / 2"
        assert view.filesystem_for_button("storage-open-0") == filesystems[4]
        assert app.query_one("#storage-open-1").disabled

        prev_button = app.query_one("#storage-page-prev")
        assert app.screen.region.contains_region(prev_button.region)
        await pilot.click("#storage-page-prev")
        await pilot.pause()
        assert str(app.query_one("#storage-page-indicator").content) == "1 / 2"


@pytest.mark.asyncio
async def test_storage_capacity_critical_keeps_status_row_blank() -> None:
    app = StorageViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(StorageView)
        view.show_snapshot(
            StorageSnapshot(
                filesystems=(_filesystem("/", usage_percent=95),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        assert str(app.query_one("#storage-health").content) == "● DOWN"
        assert str(app.query_one("#storage-status").content) == ""



@pytest.mark.asyncio
async def test_storage_disks_mode_renders_health_and_opens_current_disk() -> None:
    app = StorageViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(StorageView)
        view.show_snapshot(
            StorageSnapshot(
                filesystems=(_filesystem("/", usage_percent=20),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        disks = (_disk("nvme0n1"), _disk("sda", temperature_c=36))
        view.show_disk_snapshot(
            DiskHealthSnapshot(
                disks=disks,
                collected_at=datetime.now(timezone.utc),
            )
        )

        await pilot.click("#storage-mode-disks")
        await pilot.pause()

        assert view.mode == "disks"
        assert str(app.query_one("#storage-mode-files").label) == "FILES"
        assert str(app.query_one("#storage-mode-disks").label) == "[ DISKS ]"
        assert app.query_one("#storage-grid").has_class("hidden")
        assert not app.query_one("#storage-disks-grid").has_class("hidden")
        first_label = str(app.query_one("#storage-disk-open-0").label)
        assert "nvme0n1" in first_label
        assert "41°C" in first_label
        assert "OK" in first_label
        assert "NVME" in first_label
        assert "wear 5%" in first_label
        assert view.disk_for_button("storage-disk-open-1") == disks[1]


@pytest.mark.asyncio
async def test_storage_disk_failure_degrades_global_health() -> None:
    app = StorageViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(StorageView)
        view.show_snapshot(
            StorageSnapshot(
                filesystems=(_filesystem("/", usage_percent=20),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        view.show_disk_snapshot(
            DiskHealthSnapshot(
                disks=(_disk("sda", smart_passed=False),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        assert str(app.query_one("#storage-health").content) == "● DOWN"


@pytest.mark.asyncio
async def test_storage_disks_mode_reports_optional_smartctl_unavailable() -> None:
    app = StorageViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(StorageView)
        view.show_snapshot(
            StorageSnapshot(
                filesystems=(_filesystem("/", usage_percent=20),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        view.show_disk_snapshot(
            DiskHealthSnapshot(
                disks=(),
                collected_at=datetime.now(timezone.utc),
                available=False,
                error="smartctl not installed",
            )
        )

        await pilot.click("#storage-mode-disks")
        await pilot.pause()

        assert str(app.query_one("#storage-status").content) == "smartctl not installed"
        # Optional tooling absence must not turn otherwise healthy storage red.
        assert str(app.query_one("#storage-health").content) == "● OK"


@pytest.mark.asyncio
async def test_disk_detail_renders_smart_nvme_information() -> None:
    disk = _disk()

    class DetailApp(App[None]):
        CSS_PATH = "../src/homelab_console/console.tcss"

        def on_mount(self) -> None:
            self.screen.add_class("compact-touch")
            self.push_screen(DiskDetailScreen(disk))

    app = DetailApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()

        assert isinstance(app.screen, DiskDetailScreen)
        lines = [
            str(widget.content)
            for widget in app.screen.query(".disk-detail-line")
        ]
        joined = "\n".join(lines)

        assert "DEVICE      /dev/nvme0n1" in joined
        assert "MODEL       Example Disk" in joined
        assert "SERIAL      SER123" in joined
        assert "PROTOCOL    NVMe" in joined
        assert "TEMP        41°C" in joined
        assert "POWER ON    1000 h" in joined
        assert "WEAR USED   5%" in joined

        back = app.screen.query_one("#disk-detail-back")
        assert app.screen.region.contains_region(back.region)



@pytest.mark.asyncio
async def test_storage_subnav_is_centered_and_touchable_at_64x18() -> None:
    app = StorageViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(StorageView)
        view.show_snapshot(
            StorageSnapshot(
                filesystems=(_filesystem("/", usage_percent=20),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        view.show_disk_snapshot(
            DiskHealthSnapshot(
                disks=(_disk("nvme0"),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        files = app.query_one("#storage-mode-files")
        disks = app.query_one("#storage-mode-disks")

        assert str(files.label) == "[ FILES ]"
        assert str(disks.label) == "DISKS"
        assert files.region.height == 2
        assert disks.region.height == 2
        assert app.screen.region.contains_region(files.region)
        assert app.screen.region.contains_region(disks.region)

        # Two equal flexible spacer/status zones keep the selector pair centered.
        selector = app.query_one("#storage-mode-selector")
        pair_center = (files.region.x + disks.region.right) / 2
        selector_center = selector.region.x + selector.region.width / 2
        assert abs(pair_center - selector_center) <= 1

        await pilot.click("#storage-mode-disks")
        await pilot.pause()
        assert str(files.label) == "FILES"
        assert str(disks.label) == "[ DISKS ]"



@pytest.mark.asyncio
async def test_storage_disk_row_falls_back_to_compact_size_without_wear() -> None:
    app = StorageViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(StorageView)
        view.show_snapshot(
            StorageSnapshot(
                filesystems=(_filesystem("/", usage_percent=20),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        disk = DiskHealthInfo(
            device="/dev/sda",
            name="sda",
            model="Example HDD",
            serial="HDD123",
            protocol="ATA",
            size=2 * 1024**4,
            temperature_c=36,
            smart_passed=True,
            power_on_hours=5000,
            power_cycles=120,
            percentage_used=None,
        )
        view.show_disk_snapshot(
            DiskHealthSnapshot(
                disks=(disk,),
                collected_at=datetime.now(timezone.utc),
            )
        )

        await pilot.click("#storage-mode-disks")
        await pilot.pause()

        label = str(app.query_one("#storage-disk-open-0").label)
        assert "sda" in label
        assert "36°C" in label
        assert "OK" in label
        assert "ATA" in label
        assert "2.0T" in label
        assert app.screen.region.contains_region(
            app.query_one("#storage-disk-open-0").region
        )

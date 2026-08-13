from homelab_console.screens.host import InlineBar, ResourceMetricRow


def test_resource_threshold_configuration():
    cpu = ResourceMetricRow("CPU", warning_at=50, critical_at=80, id="cpu")
    mem = ResourceMetricRow("MEM", warning_at=60, critical_at=85, id="mem")
    disk = ResourceMetricRow("DISK", warning_at=75, critical_at=90, id="disk")
    assert (cpu.warning_at, cpu.critical_at) == (50, 80)
    assert (mem.warning_at, mem.critical_at) == (60, 85)
    assert (disk.warning_at, disk.critical_at) == (75, 90)


def test_inline_bar_threshold_configuration():
    bar = InlineBar(warning_at=70, critical_at=90)
    assert bar.warning_at == 70
    assert bar.critical_at == 90

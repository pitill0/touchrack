from homelab_console.touch import TouchDeviceInfo, choose_touch_device, map_axis


def test_map_axis_covers_full_touch_terminal_range() -> None:
    assert map_axis(0, 0, 4096, 64) == 0
    assert map_axis(4096, 0, 4096, 64) == 63
    assert map_axis(0, 0, 4096, 18) == 0
    assert map_axis(4096, 0, 4096, 18) == 17
    assert map_axis(2048, 0, 4096, 64) == 32
    assert map_axis(2048, 0, 4096, 18) == 9


def test_choose_touch_device_prefers_vid_pid_over_event_number() -> None:
    selected = choose_touch_device(
        [
            TouchDeviceInfo('/dev/input/event3', 'Other', 1, 2),
            TouchDeviceInfo('/dev/input/event9', 'Panel', 0x1A86, 0xE5E3),
        ]
    )
    assert selected is not None
    assert selected.path == '/dev/input/event9'


def test_choose_touch_device_falls_back_to_known_name() -> None:
    selected = choose_touch_device(
        [TouchDeviceInfo('/dev/input/event5', 'wch.cn USB2IIC_CTP_CONTROL')]
    )
    assert selected is not None
    assert selected.path == '/dev/input/event5'

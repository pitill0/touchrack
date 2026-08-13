from __future__ import annotations

import os
import select
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


TARGET_NAME = "wch.cn USB2IIC_CTP_CONTROL"
TARGET_VENDOR = 0x1A86
TARGET_PRODUCT = 0xE5E3


@dataclass(frozen=True, slots=True)
class TouchDeviceInfo:
    path: str
    name: str
    vendor: int | None = None
    product: int | None = None


@dataclass(frozen=True, slots=True)
class TouchTap:
    raw_x: int
    raw_y: int
    start_x: int
    start_y: int


def map_axis(value: int, minimum: int, maximum: int, output_size: int) -> int:
    """Map an absolute input value to a zero-based terminal coordinate."""
    if output_size <= 0:
        raise ValueError("output_size must be positive")
    if maximum <= minimum:
        raise ValueError("maximum must be greater than minimum")

    clamped = max(minimum, min(value, maximum))
    ratio = (clamped - minimum) / (maximum - minimum)
    mapped = int(ratio * output_size)
    return max(0, min(mapped, output_size - 1))


def choose_touch_device(devices: Iterable[TouchDeviceInfo]) -> TouchDeviceInfo | None:
    """Select the known panel without relying on an unstable event number."""
    candidates = tuple(devices)
    for device in candidates:
        if device.vendor == TARGET_VENDOR and device.product == TARGET_PRODUCT:
            return device
    for device in candidates:
        if device.name.casefold() == TARGET_NAME.casefold():
            return device
    return None


def discover_touch_device() -> TouchDeviceInfo | None:
    """Discover the touchscreen through python-evdev when available."""
    try:
        from evdev import InputDevice, list_devices
    except ImportError:
        return None

    discovered: list[TouchDeviceInfo] = []
    for path in list_devices():
        try:
            device = InputDevice(path)
            info = device.info
            discovered.append(
                TouchDeviceInfo(
                    path=path,
                    name=device.name or Path(path).name,
                    vendor=getattr(info, "vendor", None),
                    product=getattr(info, "product", None),
                )
            )
            device.close()
        except OSError:
            continue
    return choose_touch_device(discovered)


class TouchReader:
    """Read one direct-input touchscreen on a background thread.

    The class is intentionally UI-agnostic. It emits a tap only after a short
    press-and-release with limited movement. Textual integration lives in App.
    """

    def __init__(
        self,
        callback: Callable[[TouchTap], None],
        *,
        touch_down_callback: Callable[[], None] | None = None,
        device_path: str | None = None,
        movement_threshold: int = 180,
    ) -> None:
        self.callback = callback
        self.touch_down_callback = touch_down_callback
        self.device_path = device_path
        self.movement_threshold = movement_threshold
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None
        self.device_name: str | None = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True

        if self.device_path is None:
            discovered = discover_touch_device()
            if discovered is None:
                self.error = "touch device not found (or evdev unavailable)"
                return False
            self.device_path = discovered.path
            self.device_name = discovered.name

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="homelab-touch-reader",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        try:
            from evdev import InputDevice, ecodes

            device = InputDevice(self.device_path)
            self.device_name = device.name or self.device_name
            x = y = 0
            start_x = start_y = 0
            touching = False
            moved = False

            try:
                while not self._stop.is_set():
                    readable, _, _ = select.select([device.fd], [], [], 0.25)
                    if not readable:
                        continue
                    for event in device.read():
                        if event.type == ecodes.EV_ABS:
                            if event.code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                                x = event.value
                            elif event.code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                                y = event.value

                            if touching and (
                                abs(x - start_x) > self.movement_threshold
                                or abs(y - start_y) > self.movement_threshold
                            ):
                                moved = True

                        elif event.type == ecodes.EV_KEY and event.code == ecodes.BTN_TOUCH:
                            if event.value == 1:
                                if self.touch_down_callback is not None:
                                    self.touch_down_callback()
                                touching = True
                                moved = False
                                start_x, start_y = x, y
                            elif event.value == 0 and touching:
                                touching = False
                                if not moved:
                                    self.callback(TouchTap(x, y, start_x, start_y))
            finally:
                device.close()
        except Exception as exc:  # device boundary: report state, do not kill UI
            self.error = f"{type(exc).__name__}: {exc}"


def touch_enabled_from_environment() -> bool:
    return os.environ.get("HOMELAB_TOUCH", "1").strip().casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }

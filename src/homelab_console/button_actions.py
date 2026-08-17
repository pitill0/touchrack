from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ButtonActionKind(StrEnum):
    MENU = "menu"
    REFRESH_INTERVAL = "refresh_interval"
    SCREEN_BLANK = "screen_blank"
    CONTAINERS_VIEW = "containers_view"
    CONTAINERS_PAGE = "containers_page"
    CONTAINER_SORT = "container_sort"
    CONTAINER_OPEN = "container_open"
    SERVICE_OPEN = "service_open"
    STORAGE_OPEN = "storage_open"
    STORAGE_DISK_OPEN = "storage_disk_open"
    NAVIGATION = "navigation"


@dataclass(frozen=True, slots=True)
class ButtonAction:
    kind: ButtonActionKind
    value: str | int | None = None


_MENU_BUTTONS = frozenset(
    {
        "menu-button",
        "menu-button-containers",
        "menu-button-services",
        "menu-button-storage",
    }
)

_REFRESH_BUTTONS = frozenset(
    {
        "refresh-interval-host",
        "refresh-interval-containers",
        "refresh-interval-services",
        "refresh-interval-storage",
    }
)

_SCREEN_BLANK_BUTTONS = frozenset(
    {
        "screen-blank-host",
        "screen-blank-containers",
        "screen-blank-services",
        "screen-blank-storage",
    }
)


def classify_button_action(button_id: str | None) -> ButtonAction | None:
    """Translate a UI button id into a small, explicit application action."""
    if button_id is None:
        return None

    if button_id in _MENU_BUTTONS:
        return ButtonAction(ButtonActionKind.MENU)

    if button_id in _REFRESH_BUTTONS:
        return ButtonAction(ButtonActionKind.REFRESH_INTERVAL)

    if button_id in _SCREEN_BLANK_BUTTONS:
        return ButtonAction(ButtonActionKind.SCREEN_BLANK)

    if button_id == "containers-view-button":
        return ButtonAction(ButtonActionKind.CONTAINERS_VIEW)

    if button_id == "containers-page-prev":
        return ButtonAction(ButtonActionKind.CONTAINERS_PAGE, -1)

    if button_id == "containers-page-next":
        return ButtonAction(ButtonActionKind.CONTAINERS_PAGE, 1)

    if button_id.startswith("container-sort-"):
        return ButtonAction(
            ButtonActionKind.CONTAINER_SORT,
            button_id.removeprefix("container-sort-"),
        )

    if button_id.startswith("container-open-"):
        return ButtonAction(ButtonActionKind.CONTAINER_OPEN, button_id)

    if button_id.startswith("service-open-"):
        return ButtonAction(ButtonActionKind.SERVICE_OPEN, button_id)

    if button_id.startswith("storage-open-"):
        return ButtonAction(ButtonActionKind.STORAGE_OPEN, button_id)

    if button_id.startswith("storage-disk-open-"):
        return ButtonAction(ButtonActionKind.STORAGE_DISK_OPEN, button_id)

    if button_id.startswith("nav-"):
        return ButtonAction(
            ButtonActionKind.NAVIGATION,
            button_id.removeprefix("nav-"),
        )

    return None

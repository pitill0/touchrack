import pytest

from homelab_console.button_actions import (
    ButtonAction,
    ButtonActionKind,
    classify_button_action,
)


@pytest.mark.parametrize(
    ("button_id", "expected"),
    [
        ("menu-button", ButtonAction(ButtonActionKind.MENU)),
        ("menu-button-services", ButtonAction(ButtonActionKind.MENU)),
        (
            "refresh-interval-storage",
            ButtonAction(ButtonActionKind.REFRESH_INTERVAL),
        ),
        ("screen-blank-host", ButtonAction(ButtonActionKind.SCREEN_BLANK)),
        (
            "containers-view-button",
            ButtonAction(ButtonActionKind.CONTAINERS_VIEW),
        ),
        (
            "containers-page-prev",
            ButtonAction(ButtonActionKind.CONTAINERS_PAGE, -1),
        ),
        (
            "containers-page-next",
            ButtonAction(ButtonActionKind.CONTAINERS_PAGE, 1),
        ),
        (
            "container-sort-cpu",
            ButtonAction(ButtonActionKind.CONTAINER_SORT, "cpu"),
        ),
        (
            "container-open-4-name",
            ButtonAction(
                ButtonActionKind.CONTAINER_OPEN,
                "container-open-4-name",
            ),
        ),
        (
            "service-open-dns",
            ButtonAction(ButtonActionKind.SERVICE_OPEN, "service-open-dns"),
        ),
        (
            "storage-open-root",
            ButtonAction(ButtonActionKind.STORAGE_OPEN, "storage-open-root"),
        ),
        (
            "storage-disk-open-nvme0",
            ButtonAction(
                ButtonActionKind.STORAGE_DISK_OPEN,
                "storage-disk-open-nvme0",
            ),
        ),
        ("nav-host", ButtonAction(ButtonActionKind.NAVIGATION, "host")),
        (
            "nav-containers",
            ButtonAction(ButtonActionKind.NAVIGATION, "containers"),
        ),
    ],
)
def test_classify_button_action(
    button_id: str,
    expected: ButtonAction,
) -> None:
    assert classify_button_action(button_id) == expected


@pytest.mark.parametrize(
    "button_id",
    [None, "", "unknown-button", "menu-button-unknown", "nav"],
)
def test_unknown_button_ids_are_ignored(button_id: str | None) -> None:
    assert classify_button_action(button_id) is None

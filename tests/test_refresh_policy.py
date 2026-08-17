import pytest

from homelab_console.refresh_policy import (
    next_refresh_interval,
    normalize_refresh_interval,
    refresh_interval_label,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5.0, 5.0),
        (30.0, 30.0),
        (60.0, 60.0),
        (4.0, 5.0),
        (17.0, 5.0),
        (18.0, 30.0),
        (44.0, 30.0),
        (46.0, 60.0),
        (120.0, 60.0),
    ],
)
def test_normalize_refresh_interval(
    value: float,
    expected: float,
) -> None:
    assert normalize_refresh_interval(value) == expected


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (5.0, 30.0),
        (30.0, 60.0),
        (60.0, 5.0),
        (6.0, 30.0),
    ],
)
def test_next_refresh_interval_wraps(
    current: float,
    expected: float,
) -> None:
    assert next_refresh_interval(current) == expected


@pytest.mark.parametrize(
    ("compact", "expected"),
    [
        (True, "REF 30s"),
        (False, "REFRESH 30s"),
    ],
)
def test_refresh_interval_label(
    compact: bool,
    expected: str,
) -> None:
    assert refresh_interval_label(30.0, compact=compact) == expected

from __future__ import annotations

REFRESH_OPTIONS: tuple[float, ...] = (5.0, 30.0, 60.0)


def normalize_refresh_interval(value: float) -> float:
    """Return the supported refresh interval nearest to ``value``."""
    return min(
        REFRESH_OPTIONS,
        key=lambda option: abs(option - value),
    )


def next_refresh_interval(current: float) -> float:
    """Return the next supported interval, wrapping to the first."""
    normalized = normalize_refresh_interval(current)
    index = REFRESH_OPTIONS.index(normalized)
    return REFRESH_OPTIONS[(index + 1) % len(REFRESH_OPTIONS)]


def refresh_interval_label(value: float, *, compact: bool) -> str:
    """Format the refresh control without coupling policy to Textual."""
    prefix = "REF" if compact else "REFRESH"
    return f"{prefix} {int(normalize_refresh_interval(value))}s"

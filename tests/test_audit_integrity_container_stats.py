from homelab_console.providers.containers import (
    _matching_stats,
    normalize_podman_rows,
)


def test_missing_id_never_inherits_unrelated_stats():
    stats = {
        "abcdef123456": {
            "id": "abcdef123456",
            "cpu_percent": "37%",
        }
    }

    assert _matching_stats("", stats) == {}

    rows = normalize_podman_rows(
        [{"Names": ["missing-id"], "State": "running"}],
        list(stats.values()),
    )

    assert len(rows) == 1
    assert rows[0].container_id == ""
    assert rows[0].cpu_percent is None


def test_ambiguous_prefix_never_selects_first_match():
    stats = {
        "abcdef111111": {"cpu_percent": "12%"},
        "abcdef222222": {"cpu_percent": "89%"},
    }

    assert _matching_stats("abcdef", stats) == {}


def test_unique_prefix_still_resolves_stats():
    stats = {
        "abcdef123456": {"cpu_percent": "37%"},
        "123456abcdef": {"cpu_percent": "15%"},
    }

    assert _matching_stats("abcdef1234567890", stats) == {
        "cpu_percent": "37%"
    }

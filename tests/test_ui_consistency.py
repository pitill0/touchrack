from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_storage_mode_selected_uses_same_accent_text_as_containers_toggle() -> None:
    css = (ROOT / "src/homelab_console/console.tcss").read_text()

    storage_block = css.split(
        "Screen.compact-touch .storage-mode-button.selected {", 1
    )[1].split("}", 1)[0]
    containers_block = css.split(
        "Screen.compact-touch #containers-view-button {", 1
    )[1].split("}", 1)[0]

    assert "color: $primary;" in storage_block
    assert "color: $primary;" in containers_block



def test_all_pagers_have_focus_hover_and_disabled_feedback() -> None:
    css = (ROOT / "src/homelab_console/console.tcss").read_text()

    for selector in (
        ".services-page-button",
        ".storage-page-button",
        ".containers-page-button",
    ):
        assert f"{selector}:focus" in css
        assert f"{selector}:hover" in css
        assert f"{selector}:disabled" in css

    assert css.count("color: $text-disabled;") >= 3



def test_storage_mode_controls_share_the_compact_header() -> None:
    source = (ROOT / "src/homelab_console/screens/storage.py").read_text()
    css = (ROOT / "src/homelab_console/console.tcss").read_text()

    header = source.split('with Horizontal(id="storage-header"):', 1)[1].split(
        'with Grid(id="storage-grid"):', 1
    )[0]
    assert 'id="storage-mode-files"' in header
    assert 'id="storage-mode-disks"' in header
    assert 'id="storage-health"' in header
    assert 'id="refresh-interval-storage"' in header
    assert 'id="screen-blank-storage"' in header
    assert 'storage-mode-selector' not in source

    mode_block = css.split(
        "Screen.compact-touch .storage-mode-button {", 1
    )[1].split("}", 1)[0]
    assert "width: 9;" in mode_block
    assert "height: 3;" in mode_block
    assert "color: $primary;" in css.split(
        "Screen.compact-touch .storage-mode-button.selected {", 1
    )[1].split("}", 1)[0]



def test_storage_header_mode_labels_never_wrap() -> None:
    source = (ROOT / "src/homelab_console/screens/storage.py").read_text()

    assert 'Text("FILES", no_wrap=True)' in source
    assert 'Text("DISKS", no_wrap=True)' in source
    assert 'Text("FILES", no_wrap=True)' in source
    assert 'Text("DISKS", no_wrap=True)' in source

    css = (ROOT / "src/homelab_console/console.tcss").read_text()
    mode_block = css.split(
        "Screen.compact-touch .storage-mode-button {", 1
    )[1].split("}", 1)[0]
    assert "width: 9;" in mode_block
    assert "min-width: 9;" in mode_block
    assert "text-wrap: nowrap;" in mode_block
    assert "text-overflow: clip;" in mode_block



def test_contextual_controls_use_primary_without_brackets() -> None:
    storage = (ROOT / "src/homelab_console/screens/storage.py").read_text()
    containers = (ROOT / "src/homelab_console/screens/containers.py").read_text()
    css = (ROOT / "src/homelab_console/console.tcss").read_text()

    for token in ("[ FILES ]", "[ DISKS ]"):
        assert token not in storage
    for token in ("[ LIST ]", "[ BACK ]"):
        assert token not in containers

    selected = css.split(
        "Screen.compact-touch .storage-mode-button.selected {", 1
    )[1].split("}", 1)[0]
    assert "color: $primary;" in selected
    assert "text-style: bold;" in selected

    containers_block = css.split(
        "Screen.compact-touch #containers-view-button {", 1
    )[1].split("}", 1)[0]
    assert "color: $primary;" in containers_block
    assert "text-style: bold;" in containers_block

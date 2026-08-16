from pathlib import Path

from homelab_console.config import load_config


def test_config_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.display.tty == "/dev/tty1"
    assert cfg.display.sleep_minutes == 1
    assert cfg.refresh.seconds == 5
    assert cfg.touch.enabled is True
    assert cfg.touch.device == "auto"
    assert cfg.storage.mounts is None
    assert cfg.storage.disks is None


def test_config_loads_runtime_options(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
display:
  tty: /dev/tty2
  sleep: 5m
refresh:
  seconds: 60
touch:
  enabled: false
  device: /dev/input/event9
storage:
  mounts:
    - /mnt/images
    - /
    - /mnt/images
  disks:
    - /dev/sda
    - /dev/nvme0
services:
  file: custom-services.yaml
""".strip(),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.display.tty == "/dev/tty2"
    assert cfg.display.sleep_minutes == 5
    assert cfg.refresh.seconds == 60
    assert cfg.touch.enabled is False
    assert cfg.touch.device == "/dev/input/event9"
    assert cfg.storage.mounts == ("/mnt/images", "/")
    assert cfg.storage.disks == ("/dev/sda", "/dev/nvme0")
    assert cfg.services_file == str((tmp_path / "custom-services.yaml").resolve())



def test_config_rejects_non_list_storage_selection(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
storage:
  mounts: /
""".strip(),
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match=r"storage\.mounts must be a list"):
        load_config(path)


def test_config_preserves_explicit_empty_storage_lists(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
storage:
  mounts: []
  disks: []
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.storage.mounts == ()
    assert cfg.storage.disks == ()

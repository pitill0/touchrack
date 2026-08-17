# TouchRack dependency matrix

This document records the dependencies that TouchRack actually uses. It keeps
Python package requirements separate from host tools and from optional
monitoring features.

The reference deployment is Ubuntu 24.04 LTS on a physical Linux virtual
console. Other Linux distributions may provide the same binaries under
different package names.

## 1. Python runtime

Declared in `pyproject.toml` and installed automatically into the TouchRack
virtual environment:

| Dependency | Required | Used for |
| --- | --- | --- |
| Python `>=3.11` | yes | application runtime |
| `textual>=3.0,<8` | yes | TUI, screens, widgets and test driver |
| `psutil>=6.0,<8` | yes | host CPU, memory, disk and sensor information |
| `evdev>=1.7,<2` (Linux) | yes on Linux | direct touchscreen input from `/dev/input/event*` |
| `PyYAML>=6.0,<7` | yes | `config.yaml` and `services.yaml` parsing |

Touch can be disabled at runtime, but `evdev` remains a declared Linux package
dependency in the current packaging model.

## 2. Build and installation

| Dependency | Required | Used for |
| --- | --- | --- |
| Python `venv` support | supplied installer | isolated build and root-owned runtime venvs |
| `pip` inside venvs | supplied installer | wheel build and package installation |
| `setuptools>=68` | build | PEP 517 build backend |
| `wheel` | build | wheel creation |

On Ubuntu 24.04, install Python and venv support with:

```bash
sudo apt install python3 python3-venv
```

The installer does **not** require a system-wide `python3-pip`; each venv gets
its own pip. Package installation still needs access to a Python package index,
mirror or populated cache unless all required wheels are already available.

## 3. Physical Linux console

| Binary / file | Ubuntu 24.04 package | Required | Used for |
| --- | --- | --- | --- |
| `openvt` | `kbd` | supplied systemd deployment | launching TouchRack on a VT |
| `setfont` | `kbd` | reference deployment | loading the console font |
| `setterm` | `util-linux` | sleep/wake feature | blank, force blank and wake/poke |
| `Uni3-Terminus32x16.psf.gz` | `console-setup-linux` | reference 64x18 layout | 32x16 Unicode Terminus font |
| `systemctl` / systemd | `systemd` | supplied service deployment | lifecycle and systemd service checks |

Reference Ubuntu installation:

```bash
sudo apt install kbd util-linux console-setup-linux
```

`setterm` is provided by `util-linux`, not by `kbd`.

TouchRack does not require X11, Wayland, a display manager, a browser or a
desktop environment.

## 4. Optional monitoring features

### STORAGE SMART health

`smartctl` is optional. Without it, filesystem capacity monitoring continues
to work and SMART health is reported as unavailable.

Ubuntu 24.04:

```bash
sudo apt install smartmontools
```

### CONTAINERS

TouchRack auto-detects Podman first and Docker second. Neither engine is
required for the rest of the application.

Ubuntu 24.04 examples:

```bash
sudo apt install podman
# or
sudo apt install docker.io
```

An externally installed Docker Engine is also valid as long as the `docker`
CLI and daemon/socket are available to the TouchRack service.

### SERVICES providers

| Provider | External dependency |
| --- | --- |
| `container` | the detected Podman/Docker engine |
| `systemd` | `systemctl` |
| `http` | none beyond Python standard library networking |
| `tcp` | none beyond Python standard library networking |

## 5. Development and tests

Declared in the `dev` optional dependency group:

| Dependency | Used for |
| --- | --- |
| `pytest>=8.0` | test runner |
| `pytest-asyncio>=0.24` | async provider/UI tests |

Install with:

```bash
.venv/bin/pip install -e '.[dev]'
```

The suite includes Textual tests at the physical reference size of **64x18**.

## 6. What is deliberately not required

TouchRack does not require:

- X11 or Wayland
- a browser or web server
- a desktop environment
- `gpm`
- a Python Docker/Podman SDK
- `python3-pip` installed globally on the host
- `smartmontools` when SMART data is not wanted
- Docker/Podman when container monitoring is not wanted

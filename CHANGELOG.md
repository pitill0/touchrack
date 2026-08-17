# Changelog

All notable changes to TouchRack are documented here.

## [4.7.0] - 2026-08-17

### Added

- **STORAGE** as a first-class screen with filesystem capacity monitoring.
- SMART disk health, temperature, protocol/context information and disk detail
  through optional `smartctl`.
- Explicit and autodetected storage configuration for filesystems and disks.
- Services pagination for up to 12 checks per page on the 64×18 reference UI.
- Container pagination and resource/detail views tuned for the physical display.
- Runtime diagnostics, refresh-policy and button-routing modules extracted from
  the application core for clearer boundaries and testing.
- Exact dependency matrix in `docs/DEPENDENCIES.md`.
- CI coverage for Python 3.11, 3.12 and 3.13 plus wheel-content auditing.

### Changed

- STORAGE FILES/DISKS controls now live in the header, matching the contextual
  navigation pattern used by CONTAINERS.
- Contextual active controls use persistent **primary blue + bold** without
  bracket decoration, reserving warning colors for health/status semantics.
- HOST, SERVICES, STORAGE and CONTAINERS layouts were polished for the physical
  64×18 console.
- Service configuration validation is stricter and reports configuration errors
  separately from runtime outages.
- Provider refresh and subprocess cancellation paths were hardened against
  overlap, timeout and teardown races.
- Documentation and architecture diagrams were synchronized with the current
  application.

### Security

- Runtime deployment moved from the user-editable development tree to a
  root-owned `/opt/touchrack` virtual environment.
- Runtime configuration moved to root-owned `/etc/touchrack`.
- The installer builds in an isolated temporary venv and creates the final
  runtime venv directly at its installed path, avoiding stale shebangs.
- systemd hardening now includes `NoNewPrivileges`, read-only system/home
  isolation, restricted namespaces/address families, `MemoryDenyWriteExecute`,
  private temporary storage and kernel/control-group protections.
- The capability bounding set is reduced to the capabilities required by the
  physical TTY/SMART deployment.
- Runtime files use a dedicated systemd `RuntimeDirectory` and restrictive
  `UMask=0077`.
- `systemd-analyze security` improved from **9.6 UNSAFE** to **4.2 OK** on the
  reference host while retaining full physical functionality.

### Compatibility

- The installed command remains `homelab-console`.
- Existing configuration lookup compatibility is retained.
- Wake Guard behavior and the sensitive touch/blank/single-instance core remain
  unchanged.


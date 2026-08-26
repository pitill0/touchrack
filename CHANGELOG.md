# Changelog

All notable changes to TouchRack are documented here.

## [5.0.0] - 2026-08-26

### Added

- Remote Linux nodes monitored read-only through hardened system OpenSSH.
- Declarative `nodes` configuration with local and SSH transports, stable node
  identity and a touch-friendly selector for the physical 64×18 console.
- Universal node runtime composition for HOST, STORAGE, CONTAINERS, disk health
  and node-scoped SERVICES.
- Remote HOST collection from portable Linux `/proc`, `/sys`, `df` and `ip`
  interfaces without installing a TouchRack agent on the SBC.
- Remote filesystem discovery and Podman/Docker inventory and live statistics.
- Node-scoped service definitions, including remote systemd and native CRUX
  `/etc/rc.d/<service> status` providers.
- Optional remote SMART collection through a root-owned, allowlisted helper and
  exact per-device `sudoers` rules.
- Optional root-owned Podman/Docker inventory through a zero-argument,
  fixed-command helper and an exact `sudoers` rule.
- Complete multi-node configuration and service examples plus secure SSH,
  CRUX and remote SMART deployment guides.

### Changed

- TouchRack now models monitoring as Node → Transport → Provider → Snapshot,
  keeping screens independent of local or remote collection details.
- The menu and architecture documentation now describe the multi-node HOST,
  SERVICES, STORAGE and CONTAINERS console.
- Container service state reuses the already-collected snapshot for its node.
- DIAGNOSTICS identifies the active node, its transport/connectivity and the
  container access mode without triggering additional remote probes.
- Service definitions without `node` remain scoped to the local node for
  backward compatibility; explicit `node` selects a configured remote context.

### Resilience

- Per-node connectivity is tracked independently as `UP`, `DOWN` or unknown.
- HOST, STORAGE and CONTAINERS retain their last successful snapshot during a
  transport outage and mark it explicitly as `STALE` with its original age.
- A shared SSH circuit breaker suppresses redundant connection attempts and
  permits only one recovery probe after cooldown.
- Late snapshots from a previously selected node cannot replace the active
  node's UI state.
- Scheduled collection pauses while the physical display is blanked and resumes
  with one immediate active-node refresh, avoiding unnecessary SSH sessions and
  accumulated catch-up bursts.

### Security

- Remote access uses a dedicated `touchrack-monitor` account, per-node SSH
  identities, `BatchMode`, `IdentitiesOnly`, strict pinned host keys, fixed
  deadlines and no password prompts.
- SSH command construction uses fixed argument vectors with validated config;
  providers do not invoke a local shell.
- Remote SMART privilege is isolated behind a fixed root-owned helper which
  rejects multiple arguments, non-device paths and devices absent from its
  root-owned allowlist.
- No blanket `sudo`, device-group membership or write/control operations are
  required for ordinary remote monitoring.
- Root-owned container monitoring does not grant Docker socket/group access or
  arbitrary Podman/Docker commands to the SSH account.

### Compatibility

- Existing single-node configuration remains valid and creates the implicit
  local node used by TouchRack 4.7.
- Existing unscoped `services.yaml` entries continue to belong to the local
  node.
- The installed command remains `homelab-console`; wake guard, display blanking,
  direct evdev touch and single-instance behaviour are unchanged.

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

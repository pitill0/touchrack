# Remote node monitoring over SSH

TouchRack can monitor its local Linux host and remote SBCs from the same
touch-first interface. This guide uses the following example topology:

```text
TouchRack host: TouchRack Host 192.168.50.10
Remote node:    Remote SBC     192.168.50.20 (CRUX)
Transport:      OpenSSH with a dedicated touchrack-monitor account
```

Use [`config.multi-node.example.yaml`](../config.multi-node.example.yaml) and
[`services.multi-node.example.yaml`](../services.multi-node.example.yaml) as
complete starting points.

## Architecture and trust boundary

Every node is composed from the same boundaries:

```text
Node -> CommandRunner transport -> capability provider -> normalized snapshot
```

Screens only consume normalized snapshots. They do not know whether HOST,
STORAGE or CONTAINERS was collected locally or through SSH. SERVICES uses the
same node scope and transport where appropriate.

Remote monitoring is read-only. It uses the system OpenSSH client with:

- key-only authentication and `BatchMode=yes`;
- `IdentitiesOnly=yes`;
- strict host-key checking with a dedicated `known_hosts` file;
- no SSH forwarding or interactive password prompts;
- connection and command deadlines;
- a shared circuit breaker to avoid repeated SSH attempts while a node is down.

## 1. Create the dedicated remote account

On the SBC, create `touchrack-monitor` using the account-management command for
that distribution. Give it a normal non-root shell but no administrator
password and no general-purpose `sudo` permission.

Create its SSH directory:

```sh
sudo install -d -o touchrack-monitor -g touchrack-monitor -m 0700 \
  /home/touchrack-monitor/.ssh
```

## 2. Create the identity on the TouchRack host

On the TouchRack host:

```sh
sudo install -d -o root -g root -m 0700 /etc/touchrack/ssh
sudo ssh-keygen -t ed25519 -N '' \
  -f /etc/touchrack/ssh/remote_sbc_ed25519 \
  -C touchrack-monitor@remote-sbc
sudo chmod 0600 /etc/touchrack/ssh/remote_sbc_ed25519
```

Append the public key to the SBC's
`/home/touchrack-monitor/.ssh/authorized_keys`. Restrict it to the TouchRack host and
disable forwarding features:

```text
restrict,from="192.168.50.10" ssh-ed25519 AAAA... touchrack-monitor@remote-sbc
```

Then enforce ownership and permissions on the SBC:

```sh
sudo chown touchrack-monitor:touchrack-monitor \
  /home/touchrack-monitor/.ssh/authorized_keys
sudo chmod 0600 /home/touchrack-monitor/.ssh/authorized_keys
```

## 3. Pin the host key

Obtain the remote SBC host-key fingerprint through a trusted channel on the SBC:

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On the TouchRack host, collect the public host key and compare its fingerprint before
accepting it:

```sh
sudo ssh-keyscan -t ed25519 192.168.50.20 \
  | sudo tee /etc/touchrack/ssh/known_hosts >/dev/null
sudo ssh-keygen -lf /etc/touchrack/ssh/known_hosts
sudo chown root:root /etc/touchrack/ssh/known_hosts
sudo chmod 0644 /etc/touchrack/ssh/known_hosts
```

`ssh-keyscan` alone does not authenticate a host. The fingerprint comparison
is the step that prevents trusting an intercepted key.

## 4. Verify the transport

Run the same hardened connection shape used by TouchRack:

```sh
sudo ssh -T \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/etc/touchrack/ssh/known_hosts \
  -i /etc/touchrack/ssh/remote_sbc_ed25519 \
  touchrack-monitor@192.168.50.20 hostname
```

## 5. Configure nodes

The minimal shape is:

```yaml
nodes:
  - id: touchrack-host
    title: TouchRack Host
    transport:
      type: local

  - id: remote-sbc
    title: Remote SBC
    transport:
      type: ssh
      host: 192.168.50.20
      user: touchrack-monitor
      identity_file: /etc/touchrack/ssh/remote_sbc_ed25519
      known_hosts_file: /etc/touchrack/ssh/known_hosts
```

Node IDs use lowercase letters, numbers, `_` and `-`. The first configured node
is selected at startup. Only one node may use the `local` transport.

## Capabilities

| Capability | Local node | SSH node | Notes |
| --- | --- | --- | --- |
| HOST | Local provider | Universal `/proc`/`sys` collector | CPU, memory, load, root disk, temperature and IP |
| STORAGE filesystems | Configured/autodetected | Remote discovery | Read-only `df`/mount information |
| STORAGE disks | Local `smartctl` | Optional privileged helper | See [REMOTE_SMART.md](REMOTE_SMART.md) |
| CONTAINERS | Podman, then Docker | Podman, then Docker over SSH | Optional fixed helper for root-owned containers; see [REMOTE_CONTAINERS.md](REMOTE_CONTAINERS.md) |
| SERVICES `crux` | Not composed locally | `/etc/rc.d/<target> status` | No privilege escalation |
| SERVICES `systemd` | Local `systemctl` | `systemctl` over SSH | No privilege escalation |
| SERVICES `tcp`/`http` | TouchRack host | TouchRack host | The check is scoped in the UI but originates on the TouchRack host |

For CRUX, service scripts should support `status`. Example:

```yaml
  - id: remote-sbc-rpc-statd
    title: Remote SBC rpc.statd
    node: remote-sbc
    provider: crux
    target: rpc.statd
```

Dots are valid in CRUX service names. Paths, whitespace and option-like targets
are rejected.

## Failure and recovery behaviour

- `UP`, `DOWN` and `--` in the node selector represent the latest HOST probe.
- HOST, STORAGE and CONTAINERS retain their last successful snapshot after a
  transport failure and mark it visibly as `STALE`.
- Snapshot age continues from the original collection time.
- A shared SSH circuit breaker suppresses redundant attempts for five seconds.
- After cooldown, one capability probes recovery; success closes the circuit.
- Individual service failures remain visible and are never hidden by stale data.
- Automatic collection pauses while TouchRack has blanked the physical display,
  so no periodic SSH sessions are opened while the console is asleep. The wake
  touch is consumed and starts one fresh collection for the active node; missed
  intervals are not replayed.

## Deployment reminder

The systemd service executes the installed runtime under `/opt/touchrack`, not
the source checkout. After code changes, redeploy before restarting:

```sh
sudo ./scripts/install-systemd.sh
sudo systemctl restart homelab-touch-console.service
```

Runtime configuration remains in `/etc/touchrack` and is not overwritten by
subsequent deployments.

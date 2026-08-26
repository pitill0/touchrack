# Root-owned remote containers

TouchRack normally queries Podman or Docker as the unprivileged
`touchrack-monitor` SSH account. That is sufficient for rootless containers.
For root-owned containers, enable the optional read-only helper on each remote
node that needs it.

Do not add `touchrack-monitor` to the `docker` group: access to the Docker
socket is effectively root access. Do not grant wildcard `sudo` access to
Podman, Docker or this helper.

## Install the fixed helper

On the remote node, install `sudo` and the relevant container engine. Copy the
helper from the TouchRack repository and keep it root-owned:

```sh
sudo install -d -o root -g root -m 0755 /usr/local/libexec
sudo install -o root -g root -m 0755 \
  scripts/touchrack-container-inventory \
  /usr/local/libexec/touchrack-container-inventory
```

The helper accepts no arguments, uses a fixed `PATH`, prefers Podman and falls
back to Docker. Its only engine operations are fixed inventory and one-shot
statistics commands. It cannot start, stop, restart, execute, inspect or create
containers.

## Grant exactly one command

Create `/etc/sudoers.d/touchrack-container-inventory` using `visudo -f`:

```sudoers
touchrack-monitor ALL=(root) NOPASSWD: /usr/local/libexec/touchrack-container-inventory ""
```

The final `""` is significant sudoers syntax: it permits the command only
without command-line arguments. The helper independently rejects arguments as
a second boundary.

Test the exact invocation from the TouchRack host:

```sh
ssh -i /etc/touchrack/ssh/remote_sbc_ed25519 \
  touchrack-monitor@192.168.50.20 \
  sudo -n /usr/local/libexec/touchrack-container-inventory
```

It should begin with `touchrack_containers_protocol=1` and contain only the
container inventory and statistics frames.

## Enable it for one node

Add the optional capability to that SSH node in `config.yaml`:

```yaml
    containers:
      helper: /usr/local/libexec/touchrack-container-inventory
```

When `containers` is absent, TouchRack retains its existing unprivileged
Podman/Docker collection. This makes privileged collection opt-in per node and
allows rootless and root-owned container hosts to coexist.

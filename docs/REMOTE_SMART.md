# Remote SMART monitoring

Remote disk health is opt-in. TouchRack never grants its SSH user general
`sudo` access and never invokes `smartctl` directly with elevated privileges.
Instead, the remote SBC runs a small root-owned helper which accepts exactly
one device and checks it against a root-owned allowlist.

## Remote-node setup

Install `smartmontools` and `sudo`, then copy the helper from the TouchRack
repository:

```sh
sudo install -d -o root -g root -m 0755 /usr/local/libexec
sudo install -o root -g root -m 0755 scripts/touchrack-smartctl \
  /usr/local/libexec/touchrack-smartctl
```

Create `/etc/touchrack/allowed-disks`, one exact device path per line:

```text
/dev/sda
```

Protect it from the monitoring account:

```sh
sudo chown root:root /etc/touchrack/allowed-disks
sudo chmod 0644 /etc/touchrack/allowed-disks
```

Add `/etc/sudoers.d/touchrack-smartctl` with `visudo -f`. Prefer one exact
rule per allowlisted device:

```sudoers
touchrack-monitor ALL=(root) NOPASSWD: /usr/local/libexec/touchrack-smartctl /dev/sda
```

The root-owned helper additionally rejects multiple arguments, non-`/dev`
paths and devices absent from the allowlist. It uses fixed paths for its
allowlist and `smartctl`, ignoring caller environment variables.

Test from the TouchRack host:

```sh
ssh -i /etc/touchrack/ssh/remote_sbc_ed25519 \
  touchrack-monitor@192.168.50.20 \
  sudo -n /usr/local/libexec/touchrack-smartctl /dev/sda
```

Finally enable the device under that node in `config.yaml`:

```yaml
    disk_health:
      devices:
        - /dev/sda
      helper: /usr/local/libexec/touchrack-smartctl
```

SD cards and eMMC devices normally do not expose SMART data; leave
`disk_health` absent for those nodes.

#!/usr/bin/env bash
# Ubuntu 24.04 GCP experiment host. Runs only on the dedicated new VM.
set -euo pipefail
umask 027
exec > >(tee -a /var/log/supergoal-bootstrap.log) 2>&1
echo "SUPERGOAL_BOOTSTRAP_STARTED $(date -u +%FT%TZ)"
root=/var/lib/supergoal-lab
device_link=/dev/disk/by-id/google-supergoal-data
for attempt in $(seq 1 60); do
    [[ -b "$device_link" ]] && break
    sleep 2
done
[[ -b "$device_link" ]]
device=$(readlink -f "$device_link")
[[ $(blockdev --getsize64 "$device") == 214748364800 ]]
filesystem=$(blkid -s TYPE -o value "$device" || true)
if [[ -z "$filesystem" ]]; then
    # Refuse partitions, signatures, or a mounted device before formatting.
    [[ $(lsblk -nr -o NAME "$device" | wc -l) == 1 ]]
    [[ -z $(wipefs --no-act --noheadings --output TYPE "$device") ]]
    ! findmnt -rn -S "$device" >/dev/null
    mkfs.ext4 -m 0 -L supergoal-lab "$device"
else
    [[ "$filesystem" == ext4 ]]
    [[ $(blkid -s LABEL -o value "$device") == supergoal-lab ]]
fi
mkdir -p "$root"
uuid=$(blkid -s UUID -o value "$device")
if ! grep -Fq "UUID=$uuid " /etc/fstab; then
    printf 'UUID=%s %s ext4 defaults,nofail 0 2\n' "$uuid" "$root" >> /etc/fstab
fi
if ! mountpoint -q "$root"; then
    [[ -z $(find "$root" -mindepth 1 -maxdepth 1 -print -quit) ]]
    mount "$root"
fi
[[ $(findmnt -n -o UUID --target "$root") == "$uuid" ]]
install -d -m 0750 "$root/setup" "$root/probes" "$root/exports"
install -d -m 0700 "$root/private"
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=180 update
apt-get -o DPkg::Lock::Timeout=180 install -y --no-install-recommends \
    ca-certificates curl gnupg git jq rsync zstd unzip python3 python3-venv \
    qemu-system-x86 qemu-utils busybox-static cpio
curl --fail --silent --show-error --retry 3 \
    https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
    -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl --fail --silent --show-error --retry 3 \
    https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list \
    -o /etc/apt/sources.list.d/tailscale.list
# apt's unprivileged verifier must be able to read these public files.
chmod 0644 /usr/share/keyrings/tailscale-archive-keyring.gpg /etc/apt/sources.list.d/tailscale.list
apt-get -o DPkg::Lock::Timeout=180 update
apt-get -o DPkg::Lock::Timeout=180 install -y --no-install-recommends tailscale
systemctl enable --now tailscaled
modprobe kvm_intel
python3 - <<'PY'
import datetime, json, os, pathlib, platform, subprocess
root = pathlib.Path('/var/lib/supergoal-lab')
receipt = {
    'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'kernel': platform.release(), 'architecture': platform.machine(),
    'logical_cpus': os.cpu_count(),
    'memory': pathlib.Path('/proc/meminfo').read_text().splitlines()[:3],
    'data_filesystem': json.loads(subprocess.check_output(
        ['findmnt', '-J', '--target', str(root)], text=True)),
    'kvm_device_exists': pathlib.Path('/dev/kvm').exists(),
    'nested_parameter': pathlib.Path('/sys/module/kvm_intel/parameters/nested').read_text().strip(),
    'tailscale_authenticated': False,
    'model_trials_started': False,
}
(root / 'setup/gcp-bootstrap.json').write_text(json.dumps(receipt, indent=2) + '\n')
PY
echo "SUPERGOAL_BOOTSTRAP_COMPLETE $(date -u +%FT%TZ)"

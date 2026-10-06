#!/usr/bin/env bash
# Dedicated GCP lab only. Records current official package versions for a new cohort.
set -euo pipefail
umask 027
root=/var/lib/supergoal-lab
mountpoint -q "$root"
[[ -f "$root/setup/gcp-bootstrap.json" ]]
exec > >(tee -a "$root/setup/gcp-docker-install.log") 2>&1
if command -v docker >/dev/null; then
    echo 'Docker already exists; audit the existing service before changing it.' >&2
    exit 1
fi
install -d -m 0755 /etc/apt/keyrings
curl --fail --silent --show-error --retry 3 \
    https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'REPO'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
REPO
chmod 0644 /etc/apt/sources.list.d/docker.sources
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=180 update
apt-get -o DPkg::Lock::Timeout=180 install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# These services have just been installed on this empty, dedicated VM.
[[ -z $(docker ps -aq) ]]
systemctl disable --now docker.service docker.socket containerd.service
install -d -m 0750 "$root/run" "$root/docker"
cat > "$root/setup/docker-daemon.json" <<'JSON'
{
  "data-root": "/var/lib/supergoal-lab/docker",
  "exec-root": "/var/lib/supergoal-lab/run/docker",
  "pidfile": "/var/lib/supergoal-lab/run/dockerd.pid",
  "hosts": ["unix:///var/lib/supergoal-lab/run/docker.sock"],
  "bip": "172.29.224.1/24",
  "default-address-pools": [{"base": "172.28.0.0/16", "size": 24}],
  "storage-driver": "overlay2",
  "features": {"containerd-snapshotter": false},
  "log-driver": "local",
  "log-opts": {"max-size": "10m", "max-file": "2"},
  "dns": ["1.1.1.1", "8.8.8.8"]
}
JSON
cat > "$root/setup/container-network.sh" <<'NETWORK'
#!/usr/bin/env bash
set -euo pipefail
iptables -N SG-LAB-EGRESS 2>/dev/null || true
iptables -F SG-LAB-EGRESS
iptables -A SG-LAB-EGRESS -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
for destination in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
    iptables -A SG-LAB-EGRESS -d "$destination" -j DROP
done
iptables -A SG-LAB-EGRESS -j ACCEPT
for source in 172.29.224.0/24 172.28.0.0/16; do
    iptables -C FORWARD -s "$source" -j SG-LAB-EGRESS 2>/dev/null || \
        iptables -I FORWARD 1 -s "$source" -j SG-LAB-EGRESS
    iptables -C FORWARD -d "$source" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
        iptables -I FORWARD 1 -d "$source" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
    iptables -C INPUT -s "$source" -j DROP 2>/dev/null || \
        iptables -I INPUT 1 -s "$source" -j DROP
done
NETWORK
chmod 0750 "$root/setup/container-network.sh"
cat > /etc/systemd/system/supergoal-docker.service <<'SERVICE'
[Unit]
Description=Dedicated Docker daemon for Supergoal experiments
After=network-online.target tailscaled.service
Wants=network-online.target
RequiresMountsFor=/var/lib/supergoal-lab

[Service]
Type=notify
ExecStart=/usr/bin/dockerd --config-file=/var/lib/supergoal-lab/setup/docker-daemon.json
ExecStartPost=/var/lib/supergoal-lab/setup/container-network.sh
Restart=on-failure
RestartSec=5
TimeoutStartSec=180
TimeoutStopSec=120
KillMode=process
Delegate=yes
LimitNOFILE=infinity
LimitNPROC=infinity
TasksMax=infinity

[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable --now supergoal-docker.service
export DOCKER_HOST=unix:///var/lib/supergoal-lab/run/docker.sock
docker info --format '{{json .}}' > "$root/setup/gcp-docker-info.json"
docker version --format '{{json .}}' > "$root/setup/gcp-docker-version.json"
docker compose version > "$root/setup/gcp-compose-version.txt"
dpkg-query -W docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    > "$root/setup/gcp-docker-packages.tsv"
docker run --rm --name sg-infra-preflight --cpus=0.5 --memory=128m --pids-limit=128 \
    alpine:3.22 sh -eu -c '
        test "$(cat /sys/fs/cgroup/cpu.max)" = "50000 100000"
        test "$(cat /sys/fs/cgroup/memory.max)" = "134217728"
        test "$(cat /sys/fs/cgroup/pids.max)" = "128"
        wget -q -T 10 -O /dev/null https://example.com
        echo CONTAINER_NETWORK_AND_LIMITS_OK
    ' \
    > "$root/setup/gcp-container-smoke.txt"
grep -q CONTAINER_NETWORK_AND_LIMITS_OK "$root/setup/gcp-container-smoke.txt"
echo SUPERGOAL_DOCKER_READY

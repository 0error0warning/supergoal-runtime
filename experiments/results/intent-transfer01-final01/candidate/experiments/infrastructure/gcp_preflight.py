"""Zero-model-call container checks on the dedicated Linux GCP host."""
import datetime
import json
from pathlib import Path
import subprocess

root = Path('/var/lib/supergoal-lab')
docker = ['/usr/bin/docker', '--host', 'unix://' + str(root / 'run/docker.sock')]
image = json.loads(subprocess.check_output(
    docker + ['image', 'inspect', 'alpine:3.22'], text=True))[0]
probe = r'''
set -eu
test "$(cat /sys/fs/cgroup/cpu.max)" = "50000 100000"
test "$(cat /sys/fs/cgroup/memory.max)" = "134217728"
test "$(cat /sys/fs/cgroup/pids.max)" = "128"
wget -q -T 10 -O /dev/null https://example.com
if nc -z -w 2 169.254.169.254 80; then echo METADATA_WAS_REACHABLE; exit 21; fi
if nc -z -w 2 172.29.224.1 22; then echo HOST_SSH_WAS_REACHABLE; exit 22; fi
echo RESOURCE_LIMITS_HTTPS_AND_ISOLATION_PASS
'''
result = subprocess.run(docker + [
    'run', '--rm', '--name', 'sg-infra-limits-check', '--cpus=0.5',
    '--memory=128m', '--pids-limit=128', image['Id'], 'sh', '-c', probe,
], capture_output=True, text=True, timeout=45)
info = json.loads(subprocess.check_output(docker + ['info', '--format', '{{json .}}'], text=True))
version = json.loads(subprocess.check_output(docker + ['version', '--format', '{{json .}}'], text=True))
receipt = {
    'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'exit_code': result.returncode,
    'passed': result.returncode == 0 and 'RESOURCE_LIMITS_HTTPS_AND_ISOLATION_PASS' in result.stdout,
    'stdout': result.stdout, 'stderr': result.stderr,
    'docker_version': version['Server']['Version'],
    'storage_driver': info['Driver'], 'docker_root': info['DockerRootDir'],
    'cgroup_version': info['CgroupVersion'],
    'compose_version': subprocess.check_output(docker + ['compose', 'version'], text=True).strip(),
    'probe_image_id': image['Id'], 'probe_image_digests': image['RepoDigests'],
    'isolation_counters': subprocess.check_output(
        ['iptables', '-L', 'SG-LAB-EGRESS', '-n', '-v', '-x'], text=True),
    'remaining_container_ids': subprocess.check_output(docker + ['ps', '-aq'], text=True).splitlines(),
}
(root / 'setup/gcp-container-preflight.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt, indent=2))
raise SystemExit(0 if receipt['passed'] else 1)

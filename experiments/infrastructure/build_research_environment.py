"""Create one frozen, shared text/PDF tool image; no model calls."""
import json
from pathlib import Path
import subprocess


ROOT = Path('/var/lib/supergoal-lab')
docker = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]
directory = ROOT / 'setup/research-image01'
directory.mkdir()
base = 'alexgshaw/configure-git-webserver:20251031'
base_id = subprocess.check_output(docker + ['image', 'inspect', base, '--format', '{{.Id}}'], text=True).strip()
(directory / 'Dockerfile').write_text('FROM ' + base + '''
RUN find /etc/apt -type f \\( -name '*.list' -o -name '*.sources' \\) -exec sed -i 's|http://|https://|g' {} + \\
    && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends poppler-utils ripgrep python3
WORKDIR /app
''')
with (directory / 'build.log').open('xb') as log:
    subprocess.run(docker + ['build', '-t', 'sg-research-input:01', str(directory)], check=True, stdout=log, stderr=log)
image_id = subprocess.check_output(docker + ['image', 'inspect', 'sg-research-input:01', '--format', '{{.Id}}'], text=True).strip()
probe = subprocess.check_output(docker + ['run', '--rm', image_id, 'sh', '-c',
    'python3 --version; pdftotext -v; rg --version | head -1'], text=True, stderr=subprocess.STDOUT)
receipt = {'base_image': base, 'base_image_id': base_id, 'image_id': image_id, 'tool_probe': probe, 'model_calls': 0}
(directory / 'receipt.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt))

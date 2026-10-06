"""Finite host-load sample; no model calls and no changes to active trials."""
import json
from pathlib import Path
import time

ROOT = Path('/var/lib/supergoal-lab')
OUTPUT = ROOT / 'setup/host-load-mechanism01.json'


def cpu():
    return list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    rows = []
    prev = cpu()
    for _ in range(36):
        time.sleep(5)
        current = cpu()
        delta = [b-a for a, b in zip(prev, current)]
        prev = current
        mem = dict(x.split(':', 1) for x in Path('/proc/meminfo').read_text().splitlines())
        allocations = json.loads((ROOT / 'run/parallel-resource-pool.json').read_text())
        rows.append({'utc_epoch': time.time(), 'cpu_percent': 100*(sum(delta)-delta[3]-delta[4])/sum(delta),
                     'available_memory_mb': int(mem['MemAvailable'].split()[0])/1024,
                     'pool_reserved_cpus': sum(a['cpus'] for a in allocations.values()),
                     'pool_jobs': len(allocations), 'loadavg': Path('/proc/loadavg').read_text().strip(),
                     'cpu_pressure': Path('/proc/pressure/cpu').read_text().strip()})
        temp = OUTPUT.with_suffix('.tmp')
        temp.write_text(json.dumps({'status': 'running', 'samples': rows}, indent=2))
        temp.replace(OUTPUT)
    OUTPUT.write_text(json.dumps({'status': 'complete', 'samples': rows,
                     'mean_cpu_percent': sum(r['cpu_percent'] for r in rows)/len(rows),
                     'min_available_memory_mb': min(r['available_memory_mb'] for r in rows)}, indent=2))


if __name__ == '__main__':
    main()

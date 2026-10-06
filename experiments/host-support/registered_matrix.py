import json,os,subprocess,sys
from pathlib import Path
root=Path('/var/lib/supergoal-lab')
b=root/'bundles/candidate-r3'
plan=json.loads((root/'receipts/registration-holdout01.json').read_text())
for batch in plan['batches']:
    result=subprocess.run([sys.executable,str(b/'study/supervisor.py'),'--tag',batch['tag'],'--seeds',','.join(map(str,batch['seeds']))])
    if result.returncode:raise SystemExit(result.returncode)
    subprocess.run([sys.executable,str(b/'study/summarize.py'),str(root/'receipts'/(batch['tag']+'-results.json')),'--output',str(root/'receipts'/(batch['tag']+'-summary.md'))],check=True)
print('REGISTERED_MATRIX_FINISHED',flush=True)

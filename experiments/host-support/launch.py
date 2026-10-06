import json,os,subprocess,sys
from pathlib import Path
cmd=json.loads(Path(sys.argv[1]).read_text())
out=[];fds=[];i=0
while i<len(cmd):
    if cmd[i] in ('--ro-bind','--bind'):
        fd=os.open(cmd[i+1],os.O_PATH);fds.append(fd)
        out.extend(['--ro-bind-fd' if cmd[i]=='--ro-bind' else '--bind-fd',str(fd),cmd[i+2]]);i+=3
    else:
        out.append(cmd[i]);i+=1
result=subprocess.run(out,pass_fds=fds)
for fd in fds:os.close(fd)
sys.exit(result.returncode)

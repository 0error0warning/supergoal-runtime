"""One synthetic image capability check; not an accuracy benchmark."""
import base64
import json
from pathlib import Path
import subprocess
import sys

from swe2_request import request

ROOT = Path('/var/lib/supergoal-lab')
target = ROOT / 'setup/oneday-vision-probe01.json'
if target.exists():
    raise FileExistsError(target)
image = json.loads((ROOT / 'setup/oneday-assets01.json').read_text())['image_id']
code = "from PIL import Image,ImageDraw,ImageFont;import sys; im=Image.new('RGB',(360,130),'white');d=ImageDraw.Draw(im); d.text((20,25),'R7K3P9',font=ImageFont.load_default(size=52),fill='black');im.save(sys.stdout.buffer,format='PNG')"
png = subprocess.check_output(['/usr/bin/docker','--host','unix://'+str(ROOT/'run/docker.sock'),
    'run','--rm','--network','none','--cpus','1','--memory','1g','--entrypoint','python',image,'-c',code],timeout=40)
(ROOT/'setup/oneday-vision-probe01.png').write_bytes(png)
provider = json.loads((ROOT/'private/model.json').read_text())
try:
    result = request(provider, [{'type':'input_text','text':'Return only the six-character code printed in the image.'},
        {'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(png).decode()}],max_tokens=128)
    result['passed'] = result['text'].strip() == 'R7K3P9'
except Exception as exc:
    result = {'passed':False,'error':type(exc).__name__+': '+str(exc)[:500]}
result.update(synthetic=True, actual_model_requests=1)
target.write_text(json.dumps(result,indent=2))
print(json.dumps(result))
sys.exit(0 if result['passed'] else 1)

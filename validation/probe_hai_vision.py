"""Probe HAI image understanding against an existing Blender preview, without printing credentials."""
import base64
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ai_config
from studio_store import DATA, get

config=ai_config.get_config()
project=get('project','fb50b3a68d194f28af84b5d7b598599f')
output=next(o for o in reversed(project['outputs']) if o['shot_id']=='hero' and o['quality']=='preview')
path=(DATA/output['png_url'][7:]).resolve()
assert (DATA/project['id']).resolve() in path.parents
with Image.open(path) as im:
    im.thumbnail((768,768));buf=io.BytesIO();im.convert('RGB').save(buf,format='JPEG',quality=86)
image=base64.b64encode(buf.getvalue()).decode('ascii')
body={'model':'gpt-5.5-2026-04-23','messages':[{'role':'user','content':[
    {'type':'text','text':'请只根据图片简述主体物体、金属台面上的反光形状，以及红色柜体的表面反光。请使用中文。'},
    {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+image,'detail':'high'}}]}],
    'max_completion_tokens':1000,'reasoning_effort':'low'}
request=urllib.request.Request('https://api.hai.network/openai/v1/chat/completions',data=json.dumps(body).encode(),
    headers={'Content-Type':'application/json','Authorization':'Bearer '+config['api_key']})
try:
    with urllib.request.urlopen(request,timeout=120) as response: data=json.load(response)
    message=data['choices'][0]['message'].get('content') or ''
    assert message.strip(), 'Empty model response'
    report={'passed':True,'requested_model':body['model'],'response_model':data.get('model'),
        'actual_preview':output['png_url'],'answer':message,'usage':data.get('usage',{})}
    (ROOT/'validation/hai-vision-probe-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
except urllib.error.HTTPError as e:
    print('HTTP status',e.code)
    print(e.read(2048).decode('utf-8','replace').replace(config['api_key'],'[redacted]'))
    sys.exit(1)

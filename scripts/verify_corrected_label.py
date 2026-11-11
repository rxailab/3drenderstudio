"""Optional final asset quality regression through the running app API."""
import json,time,urllib.request
from pathlib import Path
BASE='http://127.0.0.1:8765';ROOT=Path(__file__).resolve().parents[1]
def call(path,data=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=60) as r:return json.load(r)
def wait(j):
    until=time.time()+1800
    while time.time()<until:
        j=call('/api/jobs/'+j['id'])
        if j['status'] in ['completed','failed','cancelled','interrupted']:
            assert j['status']=='completed',j.get('error');return j
        time.sleep(3)
    raise TimeoutError('Real render did not finish in 30 minutes')
p=call('/api/examples/matte-bottle/import',{});wait(p['jobs'][0]);p=call('/api/projects/'+p['id'])
plan=p['plan'];plan['style']='dark';plan['shots'][0]['azimuth']=10
call('/api/projects/'+p['id']+'/plan',plan)
jobs=[]
for kind,quality in [('preview','preview'),('render','standard')]:
    j=call('/api/projects/'+p['id']+'/jobs',{'kind':kind,'quality':quality,'shot_ids':['hero','campaign']});jobs.append(wait(j))
p=call('/api/projects/'+p['id'])
(ROOT/'validation/final-label-results.json').write_text(json.dumps({'project':p,'jobs':jobs},ensure_ascii=False,indent=2),encoding='utf-8')
print('Corrected asset complete',p['id'],flush=True)

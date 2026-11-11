"""Reproducible API acceptance runner. Real Blender outputs, no mocks.
Run against an already started local application: python scripts/acceptance_api.py
"""
import io
import json
import time
import urllib.request
from urllib.parse import quote
import zipfile
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8765'
REPORT=ROOT/'acceptance_results.json'

def call(path,data=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=90) as res:return json.load(res)

def wait(job):
    deadline=time.time()+1800
    while time.time()<deadline:
        j=call('/api/jobs/'+job['id'])
        if j['status'] in ['completed','failed','cancelled','interrupted']: return j
        time.sleep(3)
    raise RuntimeError('acceptance timeout')

def main():
    results=[]
    for ex in call('/api/examples'):
        p=call('/api/examples/'+ex['id']+'/import',{})
        j=wait(p['jobs'][0]);entry={'example':ex,'project_id':p['id'],'analysis_status':j['status'],'error':j.get('error')}
        if j['status']=='completed':
            p=call('/api/projects/'+p['id'])
            entry['analysis']=p['analysis']
            job=call('/api/projects/'+p['id']+'/jobs',{'kind':'preview','shot_ids':['hero'],'quality':'preview'})
            entry['preview_job']=wait(job)
            p=call('/api/projects/'+p['id']);entry['outputs']=p['outputs']
            for out in p['outputs']:
                for field in ['png_url','jpg_url']:
                    with urllib.request.urlopen(BASE+quote(out[field],safe='/:%?=&')) as res: content=res.read()
                    with Image.open(io.BytesIO(content)) as im:
                        im.load();assert im.size==(out['width'],out['height'])
            with urllib.request.urlopen(BASE+'/api/projects/'+p['id']+'/bundle?blend=0') as res: content=res.read()
            with zipfile.ZipFile(io.BytesIO(content)) as z: assert z.testzip() is None
            entry['bundle_verified']=True
        results.append(entry)
        REPORT.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(ex['id'],entry.get('preview_job',{}).get('status',j['status']),flush=True)
    print('Report:',REPORT)

if __name__=='__main__':main()

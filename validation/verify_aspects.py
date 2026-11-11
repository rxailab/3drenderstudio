"""Exercise real app API for landscape/portrait export; no mocked render results."""
import io
import json
import time
from pathlib import Path
import requests
from PIL import Image

BASE='http://127.0.0.1:8765'
OUT=Path(__file__).resolve().parent
RESULT=OUT/'aspect-results.json'
report={'started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'method':'actual localhost application API and downloaded image verification','checks':[]}

def record(): RESULT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

def api(method,path,**kw):
    response=requests.request(method,BASE+path,timeout=60,**kw)
    response.raise_for_status()
    return response.json()

def wait(job_id):
    previous=None
    start=time.time()
    while time.time()-start<2400:
        job=api('GET','/api/jobs/'+job_id)
        state=(job['status'],job.get('stage'))
        if state!=previous: print(job_id,state,flush=True);previous=state
        if job['status']=='completed': return job
        if job['status'] in ('failed','cancelled','interrupted'): raise RuntimeError(json.dumps(job,ensure_ascii=False))
        time.sleep(3)
    raise TimeoutError('render job timed out')

try:
    project=api('POST','/api/examples/metal-tray/import')
    ident=project['id'];report['project_id']=ident;record()
    print('PROJECT',ident,flush=True)
    wait(project['jobs'][0]['id'])
    for aspect,expected in [('4:3',(4096,3072)),('3:4',(3072,4096))]:
        project=api('POST','/api/projects/'+ident+'/plan',json={'aspect':aspect})
        for quality in ('preview','standard'):
            job=api('POST','/api/projects/'+ident+'/jobs',json={'kind':'preview' if quality=='preview' else 'render','quality':quality,'shot_ids':['hero']})
            report['active_job']=job['id'];record();wait(job['id'])
            project=api('GET','/api/projects/'+ident)
            output=next(o for o in project['outputs'] if o['job_id']==job['id'])
            row={'aspect':aspect,'quality':quality,'job_id':job['id'],'version':project['version'],'output':output,'download_checks':{}}
            for ext,key,fmt in [('png','png_url','PNG'),('jpg','jpg_url','JPEG')]:
                response=requests.get(BASE+output[key]+'?download=1',timeout=60);response.raise_for_status()
                saved=OUT/('metal-'+aspect.replace(':','x')+'-'+quality+'.'+ext);saved.write_bytes(response.content)
                with Image.open(saved) as image:
                    image.verify()
                with Image.open(saved) as image:
                    image.load();size=image.size;actual_format=image.format
                wanted=expected if quality=='standard' else tuple(round(x/8) for x in expected)
                assert size==wanted,(size,wanted)
                assert actual_format==fmt
                row['download_checks'][ext]={'file':str(saved),'pixels':size,'format':actual_format,'bytes':len(response.content),'valid':True}
            assert output['checks']['clipped'] is False
            row['passed']=True;report['checks'].append(row);record()
            print('VERIFIED',aspect,quality,expected,flush=True)
    report['passed']=True
    report['finished_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    report.pop('active_job',None);record()
except Exception as exc:
    report['passed']=False;report['error']=str(exc);record();raise

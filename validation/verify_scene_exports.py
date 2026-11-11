"""Integration verification against the real running local application."""
import hashlib,json,time,urllib.request,zipfile,io
from pathlib import Path
from urllib.parse import quote
ROOT=Path(__file__).resolve().parents[1];BASE='http://127.0.0.1:8765'
def call(path,body=None):
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=120) as r:return json.load(r)
project=call('/api/projects/fa395f959fd14321b60289804e3cb3af')
output=next(o for o in project['outputs'] if o['quality']=='standard' and o['shot_id']=='hero')
source=ROOT/'data'/output['blend_url'][7:];before=hashlib.sha256(source.read_bytes()).hexdigest()
records=[]
for fmt in ['blend','c4d']:
    body={'format':fmt,'output_id':output['job_id'],'shot_id':output['shot_id'],'quality':output['quality'],'version':output['version']}
    job=call('/api/projects/'+project['id']+'/exports',body)
    duplicate=call('/api/projects/'+project['id']+'/exports',body);assert duplicate['id']==job['id']
    deadline=time.time()+700
    while job['status'] in ['queued','running'] and time.time()<deadline:
        time.sleep(1);job=call('/api/exports/'+job['id'])
    assert job['status'] not in ['queued','running'],'Timeout'
    if fmt=='blend':assert job['status']=='completed' and job['native_url'].endswith('.blend'),job
    else:
        assert (job['status']=='completed' and job['native_url'].endswith('.c4d')) or (job['status']=='failed' and not job['native_url'] and job.get('conversion_package_url')),job
    url=job.get('download_url') or job.get('conversion_package_url')
    with urllib.request.urlopen(BASE+quote(url,safe='/:%?=&')) as r:binary=r.read()
    with zipfile.ZipFile(io.BytesIO(binary)) as z:
        assert z.testzip() is None
        assert 'scene.blend' in z.namelist() and 'scene.json' in z.namelist()
        if fmt=='c4d':assert 'convert_scene.py' in z.namelist()
        job['verified_zip_files']=z.namelist()
    (ROOT/'deliverables'/('Blender-editable-project.zip' if fmt=='blend' else 'Cinema4D-editable-project.zip' if job['status']=='completed' else 'Cinema4D-conversion-kit.zip')).write_bytes(binary)
    records.append(job)
assert before==hashlib.sha256(source.read_bytes()).hexdigest()
(ROOT/'validation/scene-export-integration.json').write_text(json.dumps({'source_unchanged':True,'exports':records,'capabilities':call('/api/system')['scene_exports']},ensure_ascii=False,indent=2),encoding='utf-8')
print([(x['format'],x['status'],x.get('error')) for x in records])

"""Read-only audit of an actual material-agent run started through the UI."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio_store import DATA, all_records, get

parser=argparse.ArgumentParser()
parser.add_argument('--task')
args=parser.parse_args()
task=get('material_task',args.task) if args.task else next(t for t in all_records('material_task') if t['status']=='completed')
assert task['status']=='completed', task
assert task['selection']['mode']=='ai'
assert task['use_base_color'] is False
project=get('project',task['project_id'])
job=get('job',task['job_id'])
assert job['status']=='completed' and job['shot_ids']==[task['source']['shot_id']]
output=next(o for o in project['outputs'] if o['job_id']==job['id'] and o['shot_id']==task['source']['shot_id'])
assert output['quality']=='preview'
result=json.loads((DATA/output['png_url'][7:]).with_name('result.json').read_text(encoding='utf-8'))
checks=[]
for channel,record in task['downloaded_files'].items():
    path=Path(record['path'])
    assert channel!='base_color'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
    assert path.stat().st_size==record['bytes']
    with Image.open(path) as image:
        image.load();assert list(image.size)==[record['width'],record['height']]
    checks.append({**record,'channel':channel})
resources=[resource for operation in result['operations'] for resource in operation.get('resources',[])]
assert resources and any(m['applied'] for resource in resources for m in resource['maps'])
assert all(m['channel']!='base_color' for resource in resources for m in resource['maps'])
overrides=[operation['parameters'] for operation in result['operations'] if operation.get('resources')]
assert all(override['resource']['asset_id']==task['selection']['asset_id'] for override in overrides)
old=next(h['plan'] for h in project['history'] if h['version']==task['source']['version'])
before={k:v for k,v in old.items() if k not in ('source','object_material_overrides')}
after={k:v for k,v in job['plan'].items() if k not in ('source','object_material_overrides')}
assert before==after, 'Material application changed unrelated photography settings'
targets={(t['object_name'],t['material_name']) for t in task['targets']}
assert len(targets)==1
assert {(e['object_name'],e['material_name']) for e in job['plan']['object_material_overrides']}==targets
files=[]
delivery=ROOT/'deliverables';delivery.mkdir(exist_ok=True)
for ext,fmt in [('png','PNG'),('jpg','JPEG'),('blend',None)]:
    path=DATA/output[ext+'_url'][7:]
    assert path.is_file() and path.stat().st_size>0
    if fmt:
        with Image.open(path) as image:
            image.load();assert image.size==(512,512) and image.format==fmt
    destination=delivery/('material-agent-canvas-preview.'+ext)
    shutil.copy2(path,destination)
    files.append(str(destination))
report={'passed':True,'task_id':task['id'],'project_id':task['project_id'],'job_id':job['id'],
    'selection':task['selection'],'targets':task['targets'],'downloaded_files':checks,
    'actual_resources':resources,'original_color_preserved':True,'unrelated_photography_unchanged':True,
    'native_preview_pixels':[512,512],'render_seconds':result.get('render_seconds'),'files':files,
    'review_source':output.get('review',{}).get('source'),'warnings':result.get('warnings',[]),
    'no_new_4k_validation':True}
(ROOT/'validation/material-agent-live-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
shutil.copy2(ROOT/'validation/material-agent-live-results.json',delivery/'material-agent-provenance.json')
print(json.dumps({k:report[k] for k in ('passed','task_id','selection','targets','render_seconds','review_source')},ensure_ascii=True))

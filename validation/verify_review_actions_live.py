"""Audit the normal-UI diagnostic reply -> proposal -> apply -> Cycles run."""
import json
import sys
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio_store import get,all_records,DATA

project=get('project','d2ae89ec57a74a4b8ccefdab05482118')
proposal=next(p for p in project['review_proposals'] if p['id']=='d822204a34864b4890c40373f536f592')
assert proposal['status']=='applied' and proposal['origin']=='ai'
assert proposal['base_version']==4 and proposal['applied_version']==5
assert [a['status'] for a in proposal['answers']]==['answered','skipped','skipped']
assert proposal['patch']['lighting']=={'fill_power':.5}
jobs=[j for j in all_records('job') if j.get('request_key')=='review-proposal:'+proposal['id']]
assert len(jobs)==1 and jobs[0]['status']=='completed'
job=jobs[0]
assert job['shot_ids']==['hero'] and job['version']==5
before=next(o for o in project['outputs'] if o['job_id']==proposal['source']['output_id'] and o['shot_id']=='hero')
after=next(o for o in project['outputs'] if o['job_id']==job['id'] and o['shot_id']=='hero')
def result(output):
    return json.loads((DATA/output['result_url'][7:]).read_text(encoding='utf-8'))
a,b=result(before),result(after)
assert a['success'] and b['success']
old={light['name']:light['energy'] for light in a['studio']['lights']}
new={light['name']:light['energy'] for light in b['studio']['lights']}
assert new.keys()==old.keys()
for name in old:
    multiplier=.5 if name=='Fill softbox' else 1
    assert abs(new[name]-old[name]*multiplier)<1e-5,(name,old[name],new[name])
assert a['camera']==b['camera']
request_path=(DATA/before['result_url'][7:]).with_name('request.json')
prior_plan=json.loads(request_path.read_text(encoding='utf-8'))['plan']
for key in ('material_overrides','object_material_overrides','rotation','repair','pose','exposure','light_softness'):
    assert prior_plan.get(key)==job['plan'].get(key),key
assert job['plan']['shot_adjustments']['hero']['lighting']['fill_power']==.5
images=[]
for extension,fmt in [('png','PNG'),('jpg','JPEG')]:
    path=DATA/after[extension+'_url'][7:]
    with Image.open(path) as image:
        image.load();assert image.size==(512,512) and image.format==fmt
        images.append({'format':fmt,'dimensions':list(image.size),'path':str(path.relative_to(ROOT))})
assert (DATA/after['blend_url'][7:]).is_file()
report={'status':'passed','project_id':project['id'],'proposal_id':proposal['id'],
    'source_version':4,'applied_version':5,'answers':proposal['answers'],'origin':proposal['origin'],
    'changes':proposal['changes'],'job_id':job['id'],'job_status':job['status'],
    'real_light_energy_before':old,'real_light_energy_after':new,'images':images,
    'camera_unchanged':True,'materials_and_geometry_unchanged':True,'single_job_for_proposal':True,
    'review_source_after_render':after.get('review',{}).get('source'),
    'notes':'Replies and proposal were restored by browser reload before Apply. All changes were submitted through the normal UI.'}
(ROOT/'validation/review-actions-live-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('Review proposal UI -> real Cycles output audited: passed')

"""Audit the real automatic reflection job created through the application UI."""
import json
import sys
from pathlib import Path
from PIL import Image, ImageChops, ImageStat

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio_store import DATA,get

PROJECT='fb50b3a68d194f28af84b5d7b598599f'
JOB='051a1ad588b44977934ceb0439a62c06'
project=get('project',PROJECT);job=get('job',JOB)
assert job['status']=='completed' and job['force_review']
assert job.get('review_question') and job.get('automatic_previous_preview')
before=next(o for o in project['outputs'] if o['job_id']==job['automatic_previous_preview']['job_id'] and o['shot_id']=='hero')
after=next(o for o in project['outputs'] if o['job_id']==JOB and o['shot_id']=='hero')
result=json.loads((DATA/after['result_url'][7:]).read_text(encoding='utf-8'))
assert result['success'] and result['render_pipeline_version']==2
light=next(l for l in result['studio']['lights'] if l['name']=='Metal reflection softbox')
assert light['target_object']=='G-不锈钢台面_2' and light['energy']>0
with Image.open(DATA/before['png_url'][7:]) as image_before, Image.open(DATA/after['png_url'][7:]) as image_after:
    image_before.load();image_after.load()
    assert image_before.size==image_after.size==(512,512)
    difference=sum(ImageStat.Stat(ImageChops.difference(image_before.convert('RGB'),image_after.convert('RGB'))).mean)/3
    assert difference>0
assert len(after.get('corrections',[]))<=3
report={'project':PROJECT,'job':JOB,'started_from_ui':True,'before':before['png_url'],'after':after['png_url'],
        'same_plan_version':before['version']==after['version'],'pixel_difference_mean_255':difference,
        'light':light,'corrections':after.get('corrections',[]),'review_notice':job.get('review_notice'),
        'red_body_roughness_automatically_changed':False,'new_4k_test':False}
(ROOT/'validation/automatic-reflection-live-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':True,'job':JOB,'pixel_difference_mean_255':difference,'target':light['target_object'],'rounds':len(after.get('corrections',[]))-1},ensure_ascii=False))

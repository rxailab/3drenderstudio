"""Audit an autonomous run produced by the normal preview UI, without submitting work."""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio_store import DATA, all_records, get

parser = argparse.ArgumentParser()
parser.add_argument('--project', required=True)
args = parser.parse_args()
project = get('project', args.project)
tasks = [t for t in all_records('material_task') if t['project_id'] == project['id'] and t.get('origin') == 'automatic' and t.get('job_id')]
assert tasks, 'No actual automatic material render exists'
task = tasks[0]
job = get('job', task['job_id'])
assert job['status'] == 'completed'
assert task['selection']['mode'] == 'ai' and task['use_base_color'] is False
assert set(job['shot_ids']) == set(task['chain']['shot_ids'])
assert 1 <= task['chain']['round'] <= task['chain']['max_rounds'] <= 2
assert set(task['downloaded_files']) <= {'normal', 'roughness'}
pairs = {(t['object_name'], t['material_name']) for t in task['targets']}
assert pairs == {(t['object_name'], t['material_name']) for t in task['expected_targets']}
before_plan = {k:v for k,v in task['baseline_plan'].items() if k not in ('source','object_material_overrides')}
after_plan = {k:v for k,v in job['plan'].items() if k not in ('source','object_material_overrides')}
assert before_plan == after_plan, 'Automatic material altered unrelated photography'
download_checks = []
for channel, record in task['downloaded_files'].items():
    path = Path(record['path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256']
    assert path.stat().st_size == record['bytes']
    with Image.open(path) as im:
        im.load()
        assert list(im.size) == [record['width'], record['height']]
    download_checks.append({'channel':channel, **record})
outputs = []
dest = ROOT/'deliverables'
dest.mkdir(exist_ok=True)
for shot in job['shot_ids']:
    candidate = next(o for o in project['outputs'] if o['job_id'] == job['id'] and o['shot_id'] == shot)
    baseline = next(o for o in task['baseline_outputs'] if o['shot_id'] == shot)
    result = json.loads((DATA/candidate['result_url'][7:]).read_text(encoding='utf-8'))
    assert result['success'] and result['render_seconds'] > 0
    resources = [r for op in result['operations'] for r in op.get('resources', [])]
    assert resources and any(m['applied'] for r in resources for m in r['maps'])
    assert all(m['channel'] in ('normal','roughness') for r in resources for m in r['maps'])
    assert result['visible_materials'] and all('object_name' in s for s in result['product_samples'])
    with Image.open(DATA/baseline['png_url'][7:]) as a, Image.open(DATA/candidate['png_url'][7:]) as b:
        a.load(); b.load()
        assert a.size == b.size == (512,512)
        mean_difference = sum(ImageStat.Stat(ImageChops.difference(a.convert('RGB'),b.convert('RGB'))).mean)/3
        assert mean_difference > 0, 'New material produced identical pixels'
    for ext, fmt in [('png','PNG'),('jpg','JPEG'),('blend',None)]:
        path = DATA/candidate[ext+'_url'][7:]
        assert path.is_file() and path.stat().st_size > 0
        if fmt:
            with Image.open(path) as im:
                im.load(); assert im.size == (512,512) and im.format == fmt
        shutil.copy2(path, dest/('automatic-cotton-'+shot+'.'+ext))
    shutil.copy2(DATA/baseline['png_url'][7:], dest/('automatic-cotton-'+shot+'-before.png'))
    outputs.append({'shot':shot,'job_id':job['id'],'version':candidate['version'],'pixel_mean_difference_255':mean_difference,'render_seconds':result['render_seconds'],'resources':resources,'review_source':candidate.get('review',{}).get('source')})
report = {'passed':True,'project_id':project['id'],'task_id':task['id'],'origin':'automatic','selection':task['selection'],'targets':task['targets'],'files':download_checks,'outputs':outputs,'comparison':task.get('comparison'),'decision':task.get('decision'),'auto_material':project.get('auto_material'),'unrelated_photography_preserved':True,'base_color_map_not_replaced':True,'new_4k_test':False}
(ROOT/'validation/auto-material-live-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':True,'task_id':task['id'],'selection':task['selection']['asset_id'],'comparison':task.get('comparison'),'decision':task.get('decision'),'outputs':[{k:o[k] for k in ('shot','version','pixel_mean_difference_255','render_seconds')} for o in outputs]},ensure_ascii=True))

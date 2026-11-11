"""Package verified output files and runnable source without secrets/runtime state."""
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from PIL import Image,ImageChops,ImageStat
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from studio_store import get,all_records,DATA

out=ROOT/'deliverables';out.mkdir(exist_ok=True)
p=get('project','5132c398215f4ed9aaa28cabc1e6eb30')
report={'project_id':p['id'],'checks':[],'browser_downloads':[]}
for shot in ['hero','campaign']:
    preview=next(o for o in p['outputs'] if o['shot_id']==shot and o['quality']=='preview' and o['version']==2)
    final=next(o for o in p['outputs'] if o['shot_id']==shot and o['quality']=='standard' and o['version']==2)
    src=DATA/final['png_url'][7:];prv=DATA/preview['png_url'][7:]
    a=json.loads((prv.parent/'result.json').read_text(encoding='utf-8'));b=json.loads((src.parent/'result.json').read_text(encoding='utf-8'))
    match={k:a[k]==b[k] for k in ['camera','studio','operations']};assert all(match.values())
    with Image.open(src) as im,Image.open(prv) as low:
        im.load();assert im.size==(4096,4096)
        diff=ImageStat.Stat(ImageChops.difference(im.convert('RGB').resize(low.size,Image.Resampling.LANCZOS),low.convert('RGB')))
        mae=sum(diff.mean)/3/255
    report['checks'].append({'shot':shot,'matching_settings':match,'dimensions':[4096,4096],'normalized_srgb_difference':mae,'render_seconds':b['render_seconds'],'clipped':b['checks']['clipped']})
    for kind in ['png','jpg','blend']:
        f=DATA/final[kind+'_url'][7:]
        shutil.copy2(f,out/('MORA_'+shot+'_v2_4096.'+kind))
    shutil.copy2(src.parent/'result.json',out/('MORA_'+shot+'_v2_result.json'))

downloads=Path.home()/'Downloads'
for ext,fmt in [('png','PNG'),('jpg','JPEG')]:
    f=downloads/('MORA_4K_成品验收_hero_v2.'+ext)
    with Image.open(f) as im: im.load();assert im.format==fmt and im.size==(4096,4096)
    target=out/('MORA_hero_v2_4096.'+ext)
    assert hashlib.sha256(f.read_bytes()).hexdigest()==hashlib.sha256(target.read_bytes()).hexdigest()
    report['browser_downloads'].append({'file':str(f),'format':fmt,'size':[4096,4096],'hash_matches_render':True})
bundle=downloads/'MORA_4K_成品验收_images.zip'
with zipfile.ZipFile(bundle) as z:
    assert z.testzip() is None
    report['browser_bundle']={'path':str(bundle),'entries':len(z.namelist()),'crc_verified':True}
shutil.copy2(bundle,out/'MORA_complete_images_and_blender.zip')
jobs=[j for j in all_records('job') if j['project_id']==p['id']]
report['job_history']=[{'id':j['id'],'status':j['status'],'completed':j['completed'],'reused':j.get('reused',[]),'retry_of':j.get('retry_of')} for j in jobs]
assert any(j['status']=='cancelled' for j in jobs) and any(j['status']=='interrupted' for j in jobs)
assert any(j['status']=='completed' and set(j.get('reused',[]))=={'hero'} and len(j['completed'])==2 for j in jobs)
report['resume_and_restart_verified']=True
(ROOT/'validation'/'ui-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
shutil.copy2(ROOT/'validation'/'contact-sheet.png',out/'cross-category-previews.png')
shutil.copy2(ROOT/'validation'/'workspace-final.jpg',out/'workspace-final.jpg')
corrected=ROOT/'validation/final-label-results.json'
if corrected.exists():
    result=json.loads(corrected.read_text(encoding='utf-8'));project=result['project'];entries=[]
    for shot in ['hero','campaign']:
        preview=next(o for o in project['outputs'] if o['shot_id']==shot and o['quality']=='preview')
        final=next(o for o in project['outputs'] if o['shot_id']==shot and o['quality']=='standard')
        a=json.loads((DATA/preview['png_url'][7:]).with_name('result.json').read_text(encoding='utf-8'))
        b=json.loads((DATA/final['png_url'][7:]).with_name('result.json').read_text(encoding='utf-8'))
        assert all(a[k]==b[k] for k in ['camera','studio','operations'])
        for ext in ['png','jpg','blend']:
            dest=out/('MORA_'+shot+'_final_4096.'+ext)
            shutil.copy2(DATA/final[ext+'_url'][7:],dest);entries.append(dest)
            if ext!='blend':
                with Image.open(dest) as im:im.load();assert im.size==(4096,4096)
    with zipfile.ZipFile(out/'MORA_final_4K.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in entries:z.write(f,f.name)
        z.writestr('execution-and-project.json',json.dumps(result,ensure_ascii=False,indent=2))
    report['corrected_label_project_id']=project['id']
    report['corrected_label_4k_verified']=True
    (ROOT/'validation'/'ui-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
for f in (ROOT/'validation').glob('metal-*-standard.*'):
    if f.suffix in ['.png','.jpg']:shutil.copy2(f,out/f.name)
source_files=[ROOT/name for name in ['app.py','engine.py','planner.py','studio_store.py','scene_exports.py','README.md','API.md','ACCEPTANCE.md','SCENE_EXPORT.md','requirements.txt','.env.example','start.ps1','启动应用.cmd','acceptance_results.json']]
for folder in ['static','tests','samples']:
    source_files += [f for f in (ROOT/folder).rglob('*') if f.is_file() and '__pycache__' not in f.parts and 'validation' not in f.parts]
source_files += [ROOT/'blender/worker.py',ROOT/'scripts/generate_samples.py',ROOT/'scripts/acceptance_api.py',ROOT/'scripts/contact_sheet.py',ROOT/'scripts/verify_corrected_label.py',ROOT/'validation/ui-results.json',ROOT/'validation/aspect-results.json']
if corrected.exists():source_files.append(corrected)
source_files += [ROOT/'blender/export_scene.py',ROOT/'c4d/convert_scene.py',ROOT/'blender/pick_surface.py',ROOT/'ai_client.py',ROOT/'ai_config.py',ROOT/'AI_FEEDBACK.md']
source_files += [ROOT/'validation/scene-export-integration.json',ROOT/'validation/scene-export-browser-downloads.json',ROOT/'validation/scene-export-ui.jpg',ROOT/'validation/verify_scene_exports.py',ROOT/'validation/c4d/runtime_probe.json']
source_files += [f for folder in ['validation/c4d','validation/scene-export'] for f in (ROOT/folder).glob('*.py')]
source_files += [ROOT/'validation/auto-review-results.json',ROOT/'validation/local-material-results.json',ROOT/'validation/validate_auto_review.py',ROOT/'validation/validate_local_material.py',ROOT/'validation/frontend-ai-tests.js',ROOT/'validation/ai-ui-results.json']
source_files += [f for f in (ROOT/'validation').glob('ai-*.*') if f.suffix in ('.jpg','.png')]
source_files += [ROOT/'material_edit.py',ROOT/'validation/validate_material_inspector.py',ROOT/'validation/material-inspector-results.json',ROOT/'validation/frontend-material-tests.js',ROOT/'validation/verify_material_realism_live.py',ROOT/'validation/material-realism-live-results.json',ROOT/'validation/ai-schema-debug.json']
source_files += [ROOT/'validation/diagnose_ai_schema.py',ROOT/'validation/ai-schema-retry-debug.json',ROOT/'scripts/package_delivery.py']
source_files += [ROOT/'validation/ai-cabinet-exact-debug.json']
source_files += [ROOT/'validation/cabinet-realism-live-results.json']
source_files += [ROOT/'review_actions.py',ROOT/'validation/frontend-review-actions-tests.js',ROOT/'validation/validate_lighting_controls.py',ROOT/'validation/lighting-controls-results.json',ROOT/'validation/verify_review_actions_live.py',ROOT/'validation/review-actions-live-results.json',ROOT/'validation/review-actions-ui.png',ROOT/'validation/review-actions-rendered-ui.png']
source_files += [ROOT/'material_agent.py',ROOT/'validation/frontend-material-agent-tests.js',ROOT/'validation/validate_external_materials.py',ROOT/'validation/external-materials-results.json',ROOT/'validation/verify_material_agent_live.py',ROOT/'validation/material-agent-live-results.json',ROOT/'validation/verify_material_agent_scene.py',ROOT/'validation/material-agent-scene-results.json']
source_files += [ROOT/'validation/frontend-auto-workspace-tests.js',ROOT/'validation/verify_auto_material_live.py',ROOT/'validation/generate_auto_material_sample.py',ROOT/'validation/auto-cotton-cushion.glb']
source_files += [ROOT/'validation/verify_auto_material_scene.py']
source_files += [ROOT/'asset_provenance.py',ROOT/'validation/validate_metal_material_fix.py',ROOT/'validation/metal-material-fix-results.json']
source_files += [ROOT/'validation/verify_cabinet_metal_fix_live.py',ROOT/'validation/cabinet-metal-fix-live-results.json']
source_files += [ROOT/'validation/validate_reflection_light.py',ROOT/'validation/reflection-light-results.json',ROOT/'validation/metal-reflection-live-results.json']
source_files += [ROOT/'validation/verify_reflection_live.py',ROOT/'validation/automatic-reflection-live-results.json']
source_files += [ROOT/'validation/probe_hai_vision.py',ROOT/'validation/verify_hai_vision_review.py',ROOT/'validation/hai-vision-probe-results.json',ROOT/'validation/hai-vision-structured-results.json',ROOT/'validation/hai-vision-ui-results.json']
source_files += list((ROOT/'validation').glob('hai-vision-ui*.png'))
source_files += list((ROOT/'validation').glob('automatic-reflection-workspace*.png'))
source_files += list((ROOT/'validation').glob('cabinet-metal-fix-*.png'))
source_files += list((ROOT/'validation').glob('auto-material-scene-results.json'))
source_files += [f for pattern in ['auto-material-live-results.json','automatic-workspace*.png','preview-update-results.json'] for f in (ROOT/'validation').glob(pattern)]
source_files += [f for pattern in ['preview-workspace-*.png','material-agent-workspace-ui.png','preview-workspace-results.json'] for f in (ROOT/'validation').glob(pattern)]
source_files += [f for pattern in ['material-editor-ui*.png','realism-review-ui*.png'] for f in (ROOT/'validation').glob(pattern)]
with zipfile.ZipFile(out/'Wuying-Studio-source.zip','w',zipfile.ZIP_DEFLATED) as z:
    for f in source_files:z.write(f,f.relative_to(ROOT))
print(json.dumps(report,ensure_ascii=True,indent=2))

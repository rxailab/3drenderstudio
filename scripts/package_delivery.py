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
# --- Runnable source package --------------------------------------------------
# Application code is collected by pattern, so a new module, Blender script,
# test or static asset ships without editing this file. Only the validation
# evidence below is still selected by hand.

# Root scripts that are deliberately not delivered, each with its reason.
EXCLUDED_ROOT_SCRIPTS={
    # Maintainer tool that commits and pushes a local Git checkout. Nothing in
    # the app, tests or scripts imports it, and an unzipped delivery is not a
    # Git repository.
    'update_github.py',
}

def rel(f): return f.relative_to(ROOT).as_posix()

def packable(f):
    """False for secrets, runtime state, caches and generated files."""
    parts=f.relative_to(ROOT).parts;name=f.name.lower()
    if parts[0] in ('data','deliverables') or {'.git','.venv','__pycache__'}&set(parts): return False
    if name=='.env' or (name.startswith('.env.') and name!='.env.example'): return False
    return f.suffix.lower() not in ('.pyc','.pyo','.log','.tmp','.blend1') and name not in ('thumbs.db','desktop.ini','.ds_store')

def tree(top):
    """Every deliverable file below one top-level folder."""
    base=ROOT/top;files=[];left_out=set()
    for f in sorted(base.rglob('*')):
        if not f.is_file() or not packable(f): continue
        folders=[d for d in f.parents if base in d.parents]
        # samples/validation holds local check renders; it was never delivered.
        if any(d.name=='validation' for d in folders): continue
        # A folder with request.json and result.json is what a Blender worker
        # run wrote (blender/test_*): generated output, not application code.
        run=next((d for d in folders if (d/'request.json').is_file() and (d/'result.json').is_file()),None)
        if run: left_out.add(rel(run));continue
        files.append(f)
    for folder in sorted(left_out): print('source package: left out generated render output '+folder+'/',file=sys.stderr)
    return files

source_files=[f for f in sorted(ROOT.glob('*.py')) if f.name not in EXCLUDED_ROOT_SCRIPTS]
# Markdown docs, and the verification records the update notes refer to.
source_files += sorted(ROOT.glob('*.md'))+sorted(ROOT.glob('*_VERIFICATION*.json'))
source_files += [ROOT/name for name in ['requirements.txt','.env.example','start.ps1','启动应用.cmd','acceptance_results.json']]
for folder in ['blender','c4d','static','tests','samples','references']:source_files += tree(folder)
source_files += sorted((ROOT/'scripts').glob('*.py'))

# Validation evidence stays an explicit selection.
source_files += [ROOT/'validation/ui-results.json',ROOT/'validation/aspect-results.json']
if corrected.exists():source_files.append(corrected)
source_files += [ROOT/'validation/scene-export-integration.json',ROOT/'validation/scene-export-browser-downloads.json',ROOT/'validation/scene-export-ui.jpg',ROOT/'validation/verify_scene_exports.py',ROOT/'validation/c4d/runtime_probe.json']
source_files += [f for folder in ['validation/c4d','validation/scene-export'] for f in (ROOT/folder).glob('*.py')]
source_files += [ROOT/'validation/auto-review-results.json',ROOT/'validation/local-material-results.json',ROOT/'validation/validate_auto_review.py',ROOT/'validation/validate_local_material.py',ROOT/'validation/frontend-ai-tests.js',ROOT/'validation/ai-ui-results.json']
source_files += [f for f in (ROOT/'validation').glob('ai-*.*') if f.suffix in ('.jpg','.png')]
source_files += [ROOT/'validation/validate_material_inspector.py',ROOT/'validation/material-inspector-results.json',ROOT/'validation/frontend-material-tests.js',ROOT/'validation/verify_material_realism_live.py',ROOT/'validation/material-realism-live-results.json',ROOT/'validation/ai-schema-debug.json']
source_files += [ROOT/'validation/diagnose_ai_schema.py',ROOT/'validation/ai-schema-retry-debug.json']
source_files += [ROOT/'validation/ai-cabinet-exact-debug.json']
source_files += [ROOT/'validation/cabinet-realism-live-results.json']
source_files += [ROOT/'validation/frontend-review-actions-tests.js',ROOT/'validation/validate_lighting_controls.py',ROOT/'validation/lighting-controls-results.json',ROOT/'validation/verify_review_actions_live.py',ROOT/'validation/review-actions-live-results.json',ROOT/'validation/review-actions-ui.png',ROOT/'validation/review-actions-rendered-ui.png']
source_files += [ROOT/'validation/frontend-material-agent-tests.js',ROOT/'validation/validate_external_materials.py',ROOT/'validation/external-materials-results.json',ROOT/'validation/verify_material_agent_live.py',ROOT/'validation/material-agent-live-results.json',ROOT/'validation/verify_material_agent_scene.py',ROOT/'validation/material-agent-scene-results.json']
source_files += [ROOT/'validation/frontend-auto-workspace-tests.js',ROOT/'validation/verify_auto_material_live.py',ROOT/'validation/generate_auto_material_sample.py',ROOT/'validation/auto-cotton-cushion.glb']
source_files += [ROOT/'validation/verify_auto_material_scene.py']
source_files += [ROOT/'validation/validate_metal_material_fix.py',ROOT/'validation/metal-material-fix-results.json']
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
# Self-check before anything is written: a stale entry above, a secret, or a
# script the engine needs but the patterns miss stops the build with one clear
# message instead of a traceback halfway through the archive.
import engine
source_files=list(dict.fromkeys(source_files));names=[rel(f) for f in source_files]
required={'app.py','requirements.txt','.env.example'}|{path for paths in engine.RUNTIME_COMPONENTS.values() for path in paths}
problems=[
    ('listed in scripts/package_delivery.py but missing on disk (restore the file or remove the entry)',[n for f,n in zip(source_files,names) if not f.is_file()]),
    ('secret, runtime-state or generated files that must not be delivered',[n for f,n in zip(source_files,names) if not packable(f)]),
    ('needed to run the app (entry files and engine.RUNTIME_COMPONENTS) but not collected',sorted(required-set(names))),
]
if any(found for _,found in problems):
    raise SystemExit('Wuying-Studio-source.zip was NOT written.\n'+'\n'.join('- %s:\n    %s'%(why,'\n    '.join(found)) for why,found in problems if found))
# Written beside the target and renamed, so a failure never leaves a truncated zip.
target=out/'Wuying-Studio-source.zip';partial=target.with_name(target.name+'.tmp')
try:
    with zipfile.ZipFile(partial,'w',zipfile.ZIP_DEFLATED) as z:
        for f,name in zip(source_files,names):z.write(f,name)
    partial.replace(target)
finally:
    partial.unlink(missing_ok=True)
print('source package: %d files -> %s'%(len(names),target),file=sys.stderr)
print(json.dumps(report,ensure_ascii=True,indent=2))

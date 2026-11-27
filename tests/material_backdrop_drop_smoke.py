"""Release real material drags after backdrop edits, a restart and a new Blender checkpoint."""
import copy
import json
import subprocess
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import open_project, project_server, sphere_geometry
from studio_backdrop_picker_smoke import choose


def run():
    with project_server() as (url,projects), sync_playwright() as pw:
        import engine
        import viewport_service
        from studio_store import DATA, get, put
        project=projects['base']
        with pw.chromium.launch(channel='chrome',headless=True,args=['--use-angle=swiftshader','--enable-unsafe-swiftshader']) as browser:
            page=browser.new_page(viewport={'width':1360,'height':840})
            page.set_default_timeout(90000)
            page.add_init_script("""(() => {
                localStorage.setItem('wuying-viewport-trace','off');
                const fetch=window.fetch;
                window.fetch=async (...args)=>{
                    const res=await fetch(...args);
                    if(window.holdPlan && String(args[0]).endsWith('/plan')){
                        window.holdPlan=false;
                        await new Promise(resolve=>{window.releaseSavedPlan=resolve;});
                    }
                    return res;
                };
            })()""")
            replies=[]
            page.on('response',lambda r: replies.append(r.status) if r.url.endswith('/materials/drop') else None)
            page.goto(url);open_project(page,project)
            page.wait_for_function('studioBackdropCatalog?.presets.length>0')
            warm=page.evaluate("studioBackdropCatalog.presets.find(p=>p.id==='cologne') || studioBackdropCatalog.presets.find(p=>p.id==='silver-beam')")
            choose(page,warm)
            page.locator('#localMaterialCategory').select_option('Metal')

            def drag(index):
                card=page.locator('#localMaterialGrid .local-material-tile').nth(index)
                card_id=card.get_attribute('data-id')
                start=card.bounding_box();frame=page.locator('#realtimeViewport .realtime-canvas-frame').bounding_box()
                sphere=sphere_geometry(page,'Silver sphere')
                page.mouse.move(start['x']+start['width']/2,start['y']+start['height']/2)
                page.mouse.down();page.mouse.move(frame['x']+sphere['x'],frame['y']+sphere['y'],steps=12)
                page.wait_for_function("realtime.instance.previewTarget?.object_name==='Silver sphere'")
                page.mouse.up()
                return card_id

            def saved(card_id,base):
                try:
                    page.wait_for_function("""id=>current.plan.object_material_overrides?.some(
                        r=>r.object_name==='Silver sphere' && r.catalog_id===id)""",arg=card_id)
                except Exception:
                    print('Drop state',base,replies,page.evaluate("""() => [current.version, realtime.status,
                        document.querySelector('#toast').textContent, document.querySelector('#localMaterialHint')?.textContent]"""),flush=True)
                    raise
                p=get('project',project)
                assert p['version']==base+1 and p['history'][-1]['kind']=='material_drop',p['version']
                assert len([r for r in p['plan']['object_material_overrides'] if r['object_name']=='Silver sphere'])==1
                page.wait_for_function("realtime.status==='failed' || (realtime.status==='ready' && realtime.record.version===current.version && !studioBackdropUI?.pending)")
                assert page.evaluate('realtime.status')=='ready',page.evaluate('realtime.error')
                print('Saved drop',p['version'],p['plan']['viewport_backdrop'],replies,flush=True)
                return p

            p=saved(drag(0),2)
            assert p['plan']['viewport_backdrop']==warm['id'] and replies==[200]
            # Hold the real plan write's response while the mouse is released.
            page.evaluate('window.holdPlan=true')
            page.locator('#viewportBackdropButton').click()
            page.locator('[data-backdrop="blue-keyboard"]').click()
            page.wait_for_function("typeof window.releaseSavedPlan==='function'")
            card=drag(1)
            assert replies==[200]
            page.evaluate('window.releaseSavedPlan()')
            p=saved(card,4)
            assert p['plan']['viewport_backdrop']=='blue-keyboard' and replies==[200,200]
            # In-memory viewport tasks disappear on service restart. If the
            # original signed manifest is also unavailable, the client rebinds.
            with viewport_service._TASK_LOCK:
                viewport_service.TASKS.clear()
            with patch.object(viewport_service,'_restored_surface_manifest',return_value=None):
                p=saved(drag(2),5)
            assert replies==[200,200,409,200],replies
            # A finished image can supply a newly normalized .blend while
            # the old mesh is still on screen. This forces fresh ray picking.
            checkpoint=DATA/project/'checkpoint.blend'
            changed=DATA/project/'changed-target.blend'
            baseline=DATA/project/'checkpoint-plan.json'
            baseline.write_text(json.dumps(p['plan']),encoding='utf8')
            script="""import bpy,json,os,sys
from mathutils import Vector
sys.path.insert(0,os.getcwd())
from blender import hero_scene
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=sys.argv[-4])
with open(sys.argv[-3],encoding='utf8') as f: plan=json.load(f)
products=[obj for obj in bpy.context.scene.objects if obj.type=='MESH']
hero_scene._changed_materials(bpy.context.scene,products,plan,{},[],[])
for obj in list(bpy.context.scene.objects):
    if obj.parent is None:
        obj.scale*=1.3
        obj.location=obj.location*1.3+Vector((.4,.25,.1))
camera=bpy.data.objects.new('Product Camera',bpy.data.cameras.new('Product Camera'))
bpy.context.scene.collection.objects.link(camera)
camera.location=(.4,-9,3)
camera.rotation_euler=(Vector((.4,.25,1))-camera.location).to_track_quat('-Z','Y').to_euler()
camera.data.type='ORTHO'
camera.data.ortho_scale=7.41
bpy.context.scene.camera=camera
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-2])
a,b=bpy.data.objects['Silver sphere'],bpy.data.objects['Glass sphere']
a.location,b.location=b.location.copy(),a.location.copy()
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])"""
            subprocess.run([engine.blender_path(),'-b','--factory-startup','-t','2','--python-exit-code','1',
                            '--python-expr',script,'--',p['model_path'],str(baseline),str(checkpoint),str(changed)],check=True,
                           stdout=subprocess.DEVNULL,timeout=90)
            previous=page.evaluate('realtime.instance.snapshot()')
            # Prevent the polling UI from intentionally loading the new image
            # before the mouse release. The server still reads the real .blend.
            page.evaluate('polling=true')
            p=get('project',project)
            p['outputs'].append(dict(shot_id='hero',quality='standard',version=p['version'],job_id='checkpoint',
                                     blend_url=f'/files/{project}/checkpoint.blend',effective_plan=copy.deepcopy(p['plan'])))
            put('project',p)
            p=saved(drag(3),6)
            assert replies==[200,200,409,200,409,200],replies
            assert p['plan']['viewport_backdrop']=='blue-keyboard'
            p['outputs'].append(dict(shot_id='hero',quality='standard',version=p['version'],job_id='changed',
                                     blend_url=f'/files/{project}/changed-target.blend',effective_plan=json.loads(baseline.read_text(encoding='utf8'))))
            put('project',p)
            drag(4)
            page.wait_for_function("document.querySelector('#toast').textContent.includes('落点对应的材质槽已变化')")
            assert replies==[200,200,409,200,409,200,409] and get('project',project)['version']==7,replies
            print('Saved four real drops exactly once, refused a changed target. Camera:',previous['azimuth'])


if __name__=='__main__':
    run()

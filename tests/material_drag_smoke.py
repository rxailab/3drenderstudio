"""Run a standalone, isolated material-library smoke test with real Blender and Chromium.

    python tests/material_drag_smoke.py --screenshot validation/keyshot-material-main.png

Requires Playwright for Python and Chrome/Edge (or WUYING_TEST_CHROME).
"""
import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(screenshot,without_uv=False):
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    with tempfile.TemporaryDirectory(prefix='wuying-material-test-') as temp:
        os.environ['STUDIO_DATA'] = temp
        from app import app
        from planner import default_plan
        from studio_store import get, put

        ident = uuid.uuid4().hex
        folder = Path(temp) / ident / 'originals'
        folder.mkdir(parents=True)
        model = folder / 'matte-pump-bottle.glb'
        shutil.copyfile(ROOT / 'samples' / model.name, model)
        if without_uv:
            data=model.read_bytes();size=struct.unpack_from('<I',data,12)[0]
            gltf=json.loads(data[20:20+size])
            body=next(mesh for mesh in gltf['meshes'] if mesh['name']=='Bottle body')
            for primitive in body['primitives']:primitive['attributes'].pop('TEXCOORD_0',None)
            chunk=json.dumps(gltf,separators=(',',':')).encode()
            chunk+=b' '*(-len(chunk)%4);tail=data[20+size:]
            model.write_bytes(struct.pack('<4sIIII',b'glTF',2,20+len(chunk)+len(tail),len(chunk),0x4e4f534a)+chunk+tail)
        analysis = json.loads((ROOT / 'samples/validation/matte-pump-bottle/result.json').read_text(encoding='utf8'))
        put('project', dict(id=ident, name='泵瓶 · 材质试样', slug='material-test', model_path=str(model),
                            status='ready', analysis=analysis, plan=default_plan(), version=1, history=[],
                            outputs=[], metadata={}, created_at=time.time()))
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                candidates=[os.environ.get('WUYING_TEST_CHROME'), shutil.which('google-chrome'),shutil.which('chromium'),
                            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
                executable=next((path for path in candidates if path and Path(path).is_file()),None)
                print('Launching Chromium for the isolated project.',flush=True)
                browser=playwright.chromium.launch(executable_path=executable, headless=True,timeout=30000,
                    args=['--use-angle=swiftshader','--enable-unsafe-swiftshader'])
                try:
                    page=browser.new_page(viewport={'width':1480,'height':930},device_scale_factor=1)
                    page.set_default_timeout(60000)
                    errors=[];page.on('pageerror',lambda exc:errors.append(str(exc)))
                    page.on('console',lambda msg:errors.append(msg.text) if 'Shader Error' in msg.text else None)
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    # A previous visit to a detail shot must not displace the hero on entry.
                    page.evaluate("id => localStorage.setItem('wuying-shot-'+id,'side')", ident)
                    page.locator(f'#homeProjectGrid [data-open-project="{ident}"]').click()
                    print('Opened project from the home screen.',flush=True)
                    def wait(expression):
                        try:page.wait_for_function(expression,timeout=65000)
                        except Exception:
                            details=page.evaluate("() => [typeof current==='object' && current?.id, typeof realtime==='object' && [realtime.status,realtime.error,realtime.record?.status,realtime.instance?.librarySlots?.size],document.querySelector('#localMaterialHint').textContent,document.querySelector('#toast').textContent]")
                            print('DOM state:',ascii(details),'errors:',ascii(errors),flush=True)
                            raise
                    ready = "realtime.status==='ready' && realtime.instance?.librarySlots?.size"
                    converged = ("realtime.instance?.refinement.count > 1 && "
                                 "realtime.instance.frame.dataset.samples === realtime.instance.frame.dataset.sampleLimit")
                    wait(ready)
                    print('Real WebGL viewport is ready.',flush=True)
                    assert page.evaluate('activeShot')=='hero'
                    assert page.locator('#shotTitle').inner_text()==default_plan()['shots'][0]['label']
                    assert page.locator('#liveViewButton').get_attribute('aria-pressed') == 'true'
                    assert page.locator('#inspectorPlanTab').get_attribute('aria-selected') == 'true'
                    wait("studioBackdropUI?.selection===studioBackdropCatalog?.default && realtime.instance.scene.background===realtime.instance.plate && realtime.instance.plate?.image?.width>0")
                    rail = page.locator('#projectPanel > .project-rail')
                    library = rail.locator('#localMaterialLibrary')
                    canvas = page.locator('#realtimeViewport canvas')
                    assert library.is_visible() and canvas.is_visible()
                    left_box, canvas_box = library.bounding_box(), canvas.bounding_box()
                    assert left_box['x'] + left_box['width'] <= canvas_box['x'] + 1
                    drawer = rail.locator('#shotDrawer')
                    assert drawer.is_visible() and not drawer.evaluate('(node) => node.open')
                    assert not drawer.locator('[data-shot="side"]').is_visible()
                    wait("document.querySelector('#localMaterialCount').textContent==='953 / 953 款'")
                    first_sample = library.locator('.local-material-tile img').first
                    first_sample.wait_for()
                    wait("document.querySelector('.local-material-tile img').naturalWidth > 0")
                    wait(converged)
                    # Orbit refinement uses the baked continuous ground map throughout.
                    # Read the real cached plate so a missing shader or empty shadow cannot pass.
                    ground=page.evaluate("""() => {
                      const view=realtime.instance, target=view.studioGround.result;
                      const rgba=new Uint8Array(target.width*target.height*4);
                      view.renderer.readRenderTargetPixels(target,0,0,target.width,target.height,rgba);
                      const levels=new Set();
                      for(let i=0;i<rgba.length;i+=4)levels.add(rgba[i]);
                      return {levels:levels.size,includesWhiteGround:levels.has(0)};
                    }""")
                    assert ground['levels']>45 and ground['includesWhiteGround'],ground
                    print(f"Ground shadow contains {ground['levels']} opacity levels.",flush=True)
                    if screenshot:
                        screenshot.parent.mkdir(parents=True,exist_ok=True)
                        page.mouse.move(2, 2)
                        page.screenshot(path=str(screenshot))
                    drawer.locator('summary').click()
                    page.evaluate("() => {window.materialTestView=realtime.instance}")
                    drawer.locator('[data-shot="side"]').click()
                    wait("activeShot==='side' && document.querySelector('#shotTitle').textContent==='侧面结构'")
                    wait("materialTestView.frame.dataset.samples==='0' && !materialTestView.refinement.raf")
                    page.evaluate("() => {delete window.materialTestView}")
                    drawer.locator('[data-shot="hero"]').click()
                    wait("activeShot==='hero' && "+ready)
                    drawer.locator('summary').press('Enter')
                    assert not drawer.evaluate('(node) => node.open')
                    # Material browsing must work irrespective of the right-hand inspector tab.
                    page.locator('#inspectorReviewTab').click()
                    assert library.is_visible() and page.locator('#inspectorMaterial').is_hidden()
                    page.locator('#localMaterialCategory').select_option('Metal')
                    page.locator('#localMaterialSearch').fill('Brushed 90°')
                    card=page.get_by_role('button',name='橙色阳极氧化横向拉丝铝（90°）',exact=True)
                    card.wait_for()
                    def aim():
                        return page.evaluate("""() => {
                          const model=realtime.instance,rect=model.renderer.domElement.getBoundingClientRect();
                          for(const y of [.58,.64,.7,.53])for(const x of [.49,.43,.57,.39,.62]){
                            const px=rect.left+x*rect.width,py=rect.top+y*rect.height,t=model.materialTargetAt(px,py);
                            if(t?.object_name==='Bottle body')return {x:px,y:py,slot:t.material_name};
                          }throw Error('Cannot pick bottle body');
                        }""")
                    def start_drag():
                        box=card.bounding_box();point=aim()
                        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
                        page.mouse.down();page.mouse.move(box['x']+5,box['y']+5,steps=5)
                        page.mouse.move(point['x'],point['y'],steps=12);page.mouse.move(point['x']+1,point['y']+1)
                        wait("realtime.instance.previewTarget?.object_name==='Bottle body'")
                        return point
                    # Source identity is stable across a drag cancel, including shared materials.
                    def surface_ids():
                        return page.evaluate("""() => Array.from(realtime.instance.librarySlots.values()).map(slot =>
                          slot.bindings.map(({node,index}) => (Array.isArray(node.material)?node.material[index]:node.material).uuid))""")
                    before=surface_ids()
                    wait(converged)
                    point=start_drag()
                    assert get('project',ident)['version']==1 and surface_ids()!=before
                    assert page.evaluate("realtime.instance.frame.dataset.samples")== '0'
                    page.keyboard.press('Escape');page.mouse.up()
                    wait("!realtime.instance.previewTarget");assert surface_ids()==before
                    wait(converged)
                    assert get('project',ident)['version']==1
                    point=start_drag()
                    page.mouse.move(1,1,steps=6);wait("!realtime.instance.previewTarget");page.mouse.up()
                    assert surface_ids()==before and get('project',ident)['version']==1
                    point=start_drag()
                    page.mouse.up()
                    def wait_version(version):
                        wait(f"current?.version==={version} && realtime.record?.version==={version} && realtime.status==='ready' && realtime.instance.librarySlots.size")
                    wait_version(2)
                    rows=get('project',ident)['plan']['object_material_overrides']
                    assert len(rows)==1 and rows[0]['object_name']=='Bottle body' and rows[0]['material_name']==point['slot']
                    bound_name="""slot => {const target=realtime.instance.librarySlots.get(JSON.stringify(['Bottle body',slot]));
                      const {node,index}=target.bindings[0];return (Array.isArray(node.material)?node.material[index]:node.material).name;}"""
                    applied='Anodized Aluminum Brushed 90° Orange'
                    assert page.evaluate(bound_name,point['slot'])==applied
                    page.locator('#undoButton').click();wait_version(3)
                    assert not get('project',ident)['plan']['object_material_overrides'] and page.evaluate(bound_name,point['slot'])!=applied
                    page.locator('#redoButton').click();wait_version(4)
                    assert get('project',ident)['plan']['object_material_overrides']==rows
                    page.reload();wait_version(4)
                    assert get('project',ident)['plan']['object_material_overrides']==rows
                    assert page.evaluate(bound_name,point['slot'])==applied
                    wait(converged)
                    point=aim()
                    page.mouse.move(point['x'],point['y']);page.mouse.down()
                    page.mouse.move(point['x']+45,point['y']+8,steps=5)
                    wait("realtime.changed && realtime.instance.frame.dataset.samples==='0'")
                    page.mouse.up();wait(converged)
                    page.locator('#resetViewport').click()
                    wait("!realtime.changed && "+converged)
                    initial_width=page.evaluate("realtime.instance.renderer.domElement.width")
                    page.set_viewport_size({'width':390,'height':844})
                    assert library.is_visible() and canvas.is_visible() and not drawer.evaluate('(node) => node.open')
                    wait(f"realtime.instance.renderer.domElement.width < {initial_width} && " + converged)
                    wait("document.documentElement.scrollWidth <= innerWidth + 1")
                    first_sample.scroll_into_view_if_needed()
                    assert library.locator('#localMaterialSearch').is_visible()
                    drawer.locator('summary').click()
                    assert drawer.locator('[data-shot="side"]').is_visible()
                    assert not errors,errors
                    print('Desktop and narrow layouts, default hero, 953 materials, drag/drop, undo/redo, reload and progressive rendering passed.',flush=True)
                finally:browser.close()
        finally:
            server.shutdown();thread.join();server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--screenshot',type=Path)
    parser.add_argument('--without-uv',action='store_true',help='Exercise box projection on a UV-less body')
    args=parser.parse_args();run(args.screenshot,args.without_uv)

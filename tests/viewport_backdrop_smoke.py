"""Check backdrop framing, camera identity, material response and NVIDIA refinement."""
import io
import sys

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import project_server, open_project, sphere_geometry, disc, traced_frame
from studio_backdrops import catalog as backdrop_catalog


def check_catalog(page,view):
    from pathlib import Path
    static=Path(__file__).resolve().parents[1]/'static'
    presets=list(backdrop_catalog().values())
    ids={p['id'] for p in presets}
    to_compare={'cologne' if 'cologne' in ids else 'silver-studio', 'charcoal-headphones',
                'welcome' if 'welcome' in ids else 'blue-keyboard', presets[-1]['id']}
    sizes=[]
    for preset in presets:
        page.evaluate("""async id=>{
          const v=realtime.instance;
          await v.setStudioBackdrop(id);
          if(!v.backdrop || v.backdrop.id!==id)throw Error('Wrong backdrop: '+id);
          if(id!=='white' && (!v.plate?.image.width || !v.studioRadiance?.image.data.length))throw Error('Missing plate or environment');
          if(v.backdrop.groundShadow===0 && v.studioGround)throw Error('Ground shadow enabled unexpectedly');
        }""",preset['id'])
        if preset['id'] not in to_compare:
            continue
        frame=np.asarray(Image.open(io.BytesIO(view.screenshot())).convert('RGB'))
        h,w=frame.shape[:2]
        img=Image.open(static.parent/preset['image'].lstrip('/')).convert('RGB')
        scale=max(w/img.width,h/img.height)
        scaled=img.resize((round(scale*img.width),round(scale*img.height)),Image.Resampling.BILINEAR)
        x=(scaled.width-w)//2
        y=(scaled.height-h)//2
        plate=np.asarray(scaled.crop((x,y,x+w,y+h)))
        region=np.s_[round(.10*h):round(.22*h),round(.08*w):round(.40*w)]
        error=np.abs(frame[region].astype(float)-plate[region].astype(float)).mean()
        print(f"{preset['id']} live corner vs. source photograph: {error:.2f} RGB units",flush=True)
        assert error<6
        sizes.append(frame.shape)
    assert len(sizes)==len(to_compare) and all(s==sizes[0] for s in sizes)
    print(f"{len(presets)} local presets loaded into the same viewport",flush=True)


def run(catalog_only=False):
    # A calibrated wide backplate makes a stretched, mirrored or color-managed
    # background immediately detectable, including on a square hero camera.
    chart = np.empty((320, 960, 3), dtype=np.uint8)
    chart[:] = [203, 132, 75]
    chart[:, :380] = [50, 93, 151]
    chart[:, 580:] = [124, 151, 67]
    photo = io.BytesIO()
    Image.fromarray(chart).save(photo, format='PNG')
    cool = io.BytesIO()
    Image.fromarray(chart[:,:,::-1]).save(cool,format='PNG')
    room = np.full((64,128,3),.35,dtype='<f4')
    room[:32,12:14]=6
    fixture = dict(id='cologne', image='/static/studio-backdrops/test-chart.png',
                   environmentMap='/static/studio-backdrops/environments/test-room.bin',
                   lighting=dict(key=1, fill=.8, rim=1.1, ambient=.85, yaw=-15),
                   environmentColor='#d9b58a', floorColor='#ac895d')
    with project_server() as (url, ids), sync_playwright() as pw:
        with pw.chromium.launch(channel='chrome', headless=True, args=['--use-angle=d3d11','--enable-gpu']) as browser:
            page = browser.new_page(viewport={'width':1280,'height':830}, device_scale_factor=1)
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.on('console',lambda m:errors.append(m.text) if 'Shader Error' in m.text else None)
            page.set_default_timeout(90000)
            page.route('**/test-chart.png', lambda route: route.fulfill(content_type='image/png', body=photo.getvalue()))
            page.route('**/test-cool.png', lambda route: route.fulfill(content_type='image/png', body=cool.getvalue()))
            page.route('**/test-room.bin', lambda route: route.fulfill(content_type='application/octet-stream', body=room.tobytes()))
            page.goto(url)
            open_project(page,ids['base'])
            view = page.locator('#realtimeViewport .realtime-canvas-frame')
            page.wait_for_function("studioBackdropUI?.selection===studioBackdropCatalog?.default && realtime.instance.plate?.image?.width>0")
            if catalog_only:
                page.evaluate('realtime.instance.setTraceOptions({enabled:false})')
                check_catalog(page,view)
                assert not errors,errors
                return
            page.evaluate("""() => {
              const v=realtime.instance;
              v.setTraceOptions({enabled:false});
              for(const slot of v.librarySlots.values()){
                if(/^(Stripe |Reference panel)/.test(slot.object_name))for(const binding of slot.bindings)binding.node.visible=false;
              }
              v.invalidateTraceScene();
            }""")
            def capture():
                return np.asarray(Image.open(io.BytesIO(view.screenshot())).convert('RGB')).copy()
            mirror = sphere_geometry(page,'Silver sphere')
            glass = sphere_geometry(page,'Glass sphere')
            page.evaluate("realtime.instance.setStudioBackdrop('white')")
            white = capture()
            page.evaluate("""async preset => {
              const v=realtime.instance;
              window.backdropOriginal={camera:v.camera, model:v.model, world:v.camera.matrixWorld.toArray(), count:v.scene.children.length};
              await v.setStudioBackdrop('cologne',preset);
            }""",fixture)
            live = capture()
            traced = traced_frame(page,8)
            h,w=live.shape[:2]
            assert h>100 and w>100
            for frame in (live,traced):
                for x,expected in [(.07,[50,93,151]),(.5,[203,132,75]),(.94,[124,151,67])]:
                    color=np.median(frame[round(h*.13):round(h*.18),round(w*x):round(w*x)+3],axis=(0,1))
                    assert np.abs(color-expected).max()<=3,(color,expected)
            for name,roi in [('silver',mirror),('glass',glass)]:
                delta=np.abs(live.astype(float)-white.astype(float))[disc(live,roi)].mean()
                print(f'{name} backdrop response: {delta:.2f} mean RGB units',flush=True)
                assert delta>1
            state=page.evaluate('realtime.instance.pathTracing.state')
            print('traced',state,flush=True)
            page.evaluate("""async preset=>{
              const v=realtime.instance, tracer=v.pathTracing;
              window.beforeBackdropWorker=tracer.worker;
              window.beforeGeometryRevision=tracer.revision;
              await v.setStudioBackdrop('white');
              await v.setStudioBackdrop('cologne',{...preset,image:'/static/studio-backdrops/test-cool.png'});
            }""",fixture)
            cooled=traced_frame(page,8)
            glass_diff=(traced.astype(float)-cooled.astype(float))[disc(cooled,glass)]
            print('Traced glass response to changing only the backplate:',glass_diff.mean(axis=0),flush=True)
            assert glass_diff[:,0].mean()>7 and glass_diff[:,2].mean()<-7,glass_diff.mean(axis=0)
            assert page.evaluate('realtime.instance.pathTracing.worker===window.beforeBackdropWorker && realtime.instance.pathTracing.revision===window.beforeGeometryRevision')
            page.route('**/missing-backdrop.png',lambda route: route.fulfill(status=404))
            page.evaluate("""async preset=>{
              const v=realtime.instance;
              let rejected=false;
              try{await v.setStudioBackdrop('broken',{...preset,id:'broken',image:'/static/studio-backdrops/missing-backdrop.png'});}
              catch{rejected=true;}
              if(!rejected || v.backdrop.id!=='cologne')throw Error('A failed background replaced the active plate');
              const old=window.backdropOriginal;
              const first=v.setStudioBackdrop('second',{...preset,id:'second'});
              await v.setStudioBackdrop('white');
              await first;
              if(v.backdrop.id!=='white' || v.plate || v.scene.children.length!==old.count)throw Error('Stale backdrop or lighting');
              if(v.model!==old.model || v.camera!==old.camera || v.camera.matrixWorld.toArray().some((n,i)=>Math.abs(n-old.world[i])>1e-7))throw Error('Camera or model changed');
            }""",fixture)
            assert not errors,errors


def compare_tutorials():
    """Photograph one product with three real catalog setups at the default sample limit."""
    import json
    import struct
    from pathlib import Path
    from PIL import ImageDraw, ImageFont
    from studio_backdrop_picker_smoke import choose

    root = Path(__file__).resolve().parents[1]
    source = (root/'samples/matte-pump-bottle.glb').read_bytes()
    glb = json.loads(source[20:20+struct.unpack_from('<I',source,12)[0]])
    materials = glb['materials']
    presets = backdrop_catalog()
    ids_to_show = ('cologne' if 'cologne' in presets else 'silver-studio', 'blue-keyboard',
                   'black-orbit' if 'black-orbit' in presets else 'charcoal-stage')
    with project_server() as (url, ids):
        from studio_store import get, put
        project = get('project',ids['base'])
        Path(project['model_path']).write_bytes(source)
        project['analysis']['objects'] = [
            dict(name=node['name'],type='MESH',materials=[
                materials[p['material']]['name'] for p in glb['meshes'][node['mesh']]['primitives']
            ]) for node in glb['nodes'] if 'mesh' in node
        ]
        project['analysis']['materials'] = [dict(name=m['name'],source='model') for m in materials]
        shot = project['plan']['shots'][0]
        shot.pop('ortho_scale',None)
        shot.update(azimuth=20,elevation=8)
        put('project',project)
        with sync_playwright() as pw, pw.chromium.launch(
            channel='chrome', headless=True, args=['--use-angle=d3d11','--enable-gpu']
        ) as browser:
            page = browser.new_page(viewport={'width':1420,'height':900},device_scale_factor=1)
            page.set_default_timeout(90000)
            page.goto(url)
            open_project(page,ids['base'])
            page.wait_for_function("id=>studioBackdropUI?.selection===id",arg=catalog['default'])
            matrix = page.evaluate('realtime.instance.camera.matrixWorld.toArray()')
            frame = page.locator('#realtimeViewport .realtime-canvas-frame')
            photos = []
            for ident in ids_to_show:
                choose(page,presets[ident])
                page.wait_for_function("""() => {
                    const s = realtime.instance.pathTracing.state;
                    return s.mode==='fallback' || s.samples>=128;
                }""",timeout=240000)
                state = page.evaluate('realtime.instance.pathTracing.state')
                print(ident,state['samples'],state['adapterName'],flush=True)
                assert state['samples']==128 and 'NVIDIA' in state['adapterName'],state
                np.testing.assert_allclose(page.evaluate('realtime.instance.camera.matrixWorld.toArray()'),matrix)
                photos.append(Image.open(io.BytesIO(frame.screenshot())).convert('RGB'))
    panel = 400
    comparison = Image.new('RGB',(3*panel,panel+44),(245,245,243))
    draw = ImageDraw.Draw(comparison)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',17)
    for i,(photo,ident) in enumerate(zip(photos,ids_to_show)):
        photo.thumbnail((panel,panel),Image.Resampling.LANCZOS)
        comparison.paste(photo,(i*panel+(panel-photo.width)//2,0))
        draw.text((i*panel+14,panel+8),presets[ident]['name'],fill=(34,38,42),font=font)
    comparison.save(root/'validation/viewport-backdrops-comparison.jpg',quality=93)


if __name__=='__main__':
    if '--compare' in sys.argv:
        compare_tutorials()
    else:
        run('--catalog' in sys.argv)

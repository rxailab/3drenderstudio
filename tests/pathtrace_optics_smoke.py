"""Validate object reflections and glass refraction by comparing actual Chromium frames.

    python tests/pathtrace_optics_smoke.py
    python tests/pathtrace_optics_smoke.py --software

Three isolated projects differ only in one material field. Each uses the normal
Blender export, model loader, white hero camera and GPU viewport. Requires
Playwright, a local Chrome/Edge, Pillow and NumPy.
"""
import argparse
import contextlib
import io
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
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / 'samples' / 'material-optics.glb'


def glb_variant(model, variant):
    data = FIXTURE.read_bytes()
    size = struct.unpack_from('<I', data, 12)[0]
    doc = json.loads(data[20:20+size])
    mats = {m['name']:m for m in doc['materials']}
    if variant == 'blue':
        mats['Vermilion reference']['pbrMetallicRoughness']['baseColorFactor'] = [.012,.045,.96,1.]
    if variant == 'air':
        mats['Optical glass 1.52']['extensions']['KHR_materials_ior']['ior'] = 1.001
    chunk = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    chunk += b' ' * (-len(chunk)%4)
    tail = data[20+size:]
    model.write_bytes(struct.pack('<4sIIII', b'glTF', 2, 20+len(chunk)+len(tail),
                                  len(chunk), 0x4e4f534a) + chunk + tail)
    parts = []
    for n in doc['nodes']:
        if 'mesh' in n:
            primitives = doc['meshes'][n['mesh']]['primitives']
            parts.append({'name':n['name'], 'type':'MESH', 'materials':[
                doc['materials'][p['material']]['name'] for p in primitives]})
    return {'objects':parts, 'materials':[{'name':name, 'source':'model'} for name in mats],
            'warnings':[], 'features':{}, 'animations':[]}


@contextlib.contextmanager
def project_server(*,backdrop=None):
    from werkzeug.serving import make_server
    with tempfile.TemporaryDirectory(prefix='wuying-optics-') as directory, patch.dict(os.environ, STUDIO_DATA=directory):
        from app import app
        from planner import default_plan
        from studio_store import DATA, put
        if DATA.resolve() != Path(directory).resolve():
            raise RuntimeError('Run the optics fixture in a fresh process to isolate its project database.')
        ids = {}
        for variant, label in [('base','银与玻璃'), ('blue','蓝色参照'), ('air','低折射率参照')]:
            ident = ids[variant] = uuid.uuid4().hex
            model = Path(directory) / ident / 'originals' / 'Optics.glb'
            model.parent.mkdir(parents=True)
            analysis = glb_variant(model, variant)
            plan = default_plan()
            if backdrop is not None:
                plan['viewport_backdrop']=backdrop
            plan['shots'][0].update(azimuth=0, elevation=8, ortho_scale=5.7,
                                    projection='orthographic', target_offset=[0,0,0])
            put('project', dict(id=ident, name=label, slug='optics-'+variant, model_path=str(model),
                                status='ready', analysis=analysis, plan=plan, version=1, history=[],
                                outputs=[], metadata={}, created_at=time.time()))
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        http = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{http.server_port}/', ids
        finally:
            http.shutdown()
            thread.join()
            http.server_close()


def open_project(page, ident):
    if page.locator('#exitProjectButton').is_visible():
        page.evaluate("() => {window.previousOpticsView=realtime.instance}")
        page.locator('#exitProjectButton').click()
        page.wait_for_function("""() => {
          const v=window.previousOpticsView;
          return v.disposed && !v.frame.isConnected && !v.pathTracing?.worker;
        }""")
        page.evaluate("() => {delete window.previousOpticsView}")
    page.locator(f'#homeProjectGrid [data-open-project="{ident}"]').click()
    page.wait_for_function("realtime.status==='ready' && realtime.instance?.librarySlots.size")
    page.wait_for_function("document.querySelector('#localMaterialGrid .local-material-tile img')?.naturalWidth > 0")
    assert page.evaluate('activeShot')=='hero'
    assert page.locator('#shotTitle').inner_text()==page.evaluate("current.plan.shots.find(shot=>shot.id==='hero').label")


def traced_frame(page, samples):
    page.evaluate("""limit => realtime.instance.setTraceOptions({enabled:true,limit})""", samples)
    try:
        page.wait_for_function("""limit => {
          const d=realtime.instance?.frame.dataset;
          return d?.traceMode==='fallback' || (d?.traceMode==='pathtracing' && Number(d.traceSamples)>=limit);
        }""", arg=samples, timeout=180000)
    except Exception:
        print('Trace state:',page.evaluate("() => realtime.instance?.pathTracing?.state"),flush=True)
        raise
    state = page.evaluate("() => realtime.instance.pathTracing.state")
    assert state['mode']=='pathtracing' and state['samples']>=samples,state
    frame = page.locator('#realtimeViewport .realtime-canvas-frame')
    return np.asarray(Image.open(io.BytesIO(frame.screenshot())).convert('RGB')).copy()


def sphere_geometry(page, name):
    return page.evaluate("""name => {
      const v=realtime.instance;
      const slot=Array.from(v.librarySlots.values()).find(s=>s.object_name===name);
      if(!slot)throw Error('Missing optical sphere '+name);
      const mesh=slot.bindings[0].node;
      mesh.geometry.computeBoundingSphere();
      const sphere=mesh.geometry.boundingSphere.clone().applyMatrix4(mesh.matrixWorld);
      const center=sphere.center.clone().project(v.camera),right=sphere.center.clone();
      right.addScaledVector(right.clone().set(1,0,0).applyQuaternion(v.camera.quaternion),sphere.radius);
      right.project(v.camera);
      const rect=v.frame.getBoundingClientRect();
      const x=(center.x+1)*rect.width/2, y=(1-center.y)*rect.height/2;
      const radius=(right.x-center.x)*rect.width/2;
      for(let i=0;i<12;i++){
        const a=i*Math.PI/6, hit=v.materialTargetAt(rect.left+x+Math.cos(a)*radius*.75,rect.top+y+Math.sin(a)*radius*.75);
        if(hit?.object_name!==name)throw Error('Occluded sphere ROI: '+JSON.stringify(hit?.object_name));
      }
      return {x,y,radius,world:sphere.center.toArray()};
    }""", name)


def disc(frame, sphere):
    y,x = np.ogrid[:frame.shape[0],:frame.shape[1]]
    return (x-sphere['x'])**2 + (y-sphere['y'])**2 < (.84*sphere['radius'])**2


def white_backdrop(frame):
    # A known empty part of this hero shot, clear of the status badge.
    h,w = frame.shape[:2]
    cut = frame[int(.05*h):int(.18*h),int(.05*w):int(.45*w)]
    fraction = float((cut.min(axis=2)>=245).mean())
    assert fraction > .99, f'White hero backdrop: {fraction:.1%} of background is white'
    return fraction


def floor_shadow(page, frame, sphere):
    # Ignore any pixels occupied by model geometry. Sample the white studio
    # floor between the two plinths where the default key light casts a shadow.
    points = page.evaluate("""s => {
      const v=realtime.instance, r=v.frame.getBoundingClientRect(), points=[];
      for(let dx=.92;dx<1.62;dx+=.07)for(let dy=.90;dy<1.30;dy+=.04) {
        const x=Math.round(s.x+s.radius*dx),y=Math.round(s.y+s.radius*dy);
        if(!v.materialTargetAt(r.left+x,r.top+y))points.push([x,y]);
      }
      return points;
    }""", sphere)
    brightness = [float(frame[y,x].mean()) for x,y in points]
    result = {'tested_floor_pixels':len(brightness),'shadowed_floor_pixels':sum(v<248 for v in brightness),
              'darkest_floor_rgb_mean':min(brightness) if brightness else 255.}
    assert result['shadowed_floor_pixels']>=5 and result['darkest_floor_rgb_mean']<245,result
    return result


def inspect_glass(base, air, glass):
    roi = disc(base, glass)
    delta = np.abs(base.astype(float) - air.astype(float)).mean(axis=2)
    shifted, glass_to_reference = [], []
    stripe_widths = []
    radius = glass['radius']
    for dy in [-.3,-.15,0,.15,.3]:
        y = round(glass['y']+dy*radius)
        x = round(glass['x']-.72*radius)
        end = round(glass['x']+.72*radius)
        profiles = []
        widths = []
        for image in (base, air):
            red = image[y-1:y+2,x:end,0].mean(axis=0)
            red = np.convolve(red, [.2]*5, mode='valid')
            low, high = np.percentile(red, [15,85])
            if high-low < 12:
                break
            light = red > (low+high)/2
            profiles.append(np.flatnonzero(light[1:] != light[:-1]))
            starts = np.flatnonzero(np.diff(np.r_[0, ~light, 0].astype(int)) == 1)
            ends = np.flatnonzero(np.diff(np.r_[0, ~light, 0].astype(int)) == -1)
            widths.append([int(end-start) for start,end in zip(starts,ends) if end-start>=3])
        if len(profiles)==2 and all(len(p)>=2 for p in profiles):
            a,b = profiles
            # Refraction also combines or shifts bands outside the ROI. With
            # a periodic board, each remaining edge may be close to *some*
            # original edge despite a large warp. Measure both directions.
            distances=np.abs(a[:,None]-b[None,:])
            glass_to_reference.extend(distances.min(axis=1))
            shifted.extend(distances.min(axis=0))
            shifted.extend(distances.min(axis=1))
            if all(widths):
                stripe_widths.append((max(widths[0]), float(np.median(widths[1]))))
    edge_shift = float(np.median(shifted)) if shifted else 0.
    result = {'mean_rgb_difference':float(delta[roi].mean()), 'stripe_edges_compared':len(shifted),
              'median_symmetric_stripe_edge_shift_px':edge_shift,
              'median_stripe_edge_shift_px':float(np.median(glass_to_reference)) if glass_to_reference else 0.,
              'sample_radius_px':radius,
              'glass_dark_band_width_px':float(np.median([v[0] for v in stripe_widths])) if stripe_widths else 0.,
              'air_dark_band_width_px':float(np.median([v[1] for v in stripe_widths])) if stripe_widths else 0.}
    assert result['mean_rgb_difference'] > 5 and len(shifted)>=6 and edge_shift>=max(2,.02*radius), result
    assert result['glass_dark_band_width_px'] > 1.5*result['air_dark_band_width_px'] > 0,result
    return result


def inspect_metal(base, blue, mirror):
    roi = disc(base, mirror)
    a,b = base.astype(float),blue.astype(float)
    # Swapping the side target from red to blue must change hue INSIDE the silver sphere.
    red = a[:,:,0] - a[:,:,2]
    blue_dominance = b[:,:,2] - b[:,:,0]
    matched = roi & (red>16) & (blue_dominance>16)
    result = {'matched_reflection_pixels':int(matched.sum()), 'sphere_roi_pixels':int(roi.sum()),
              'red_to_blue_fraction':float(matched.sum()/roi.sum())}
    assert matched.sum() > max(12,roi.sum()*.003), result
    return result


def reflected_glass(base, air, mirror):
    # The neighbouring sphere is to the right of the silver at a 90° angle
    # to the viewing ray. Its mirror image lies around sin(45°) of the radius.
    delta = np.abs(base.astype(float)-air.astype(float)).mean(axis=2)
    y,x = np.ogrid[:base.shape[0],:base.shape[1]]
    u = (x-mirror['x'])/mirror['radius']
    v = (y-mirror['y'])/mirror['radius']
    right = ((u-.70)/.14)**2+(v/.20)**2 < 1
    left = ((u+.70)/.14)**2+(v/.20)**2 < 1
    result = {'glass_side_difference':float(delta[right].mean()),
              'opposite_side_difference':float(delta[left].mean()),
              'glass_side_pixels':int(right.sum())}
    assert result['glass_side_difference']>max(4,1.4*result['opposite_side_difference']),result
    return result


def write_comparison(path, base, blue, air, mirror, glass):
    font_paths = [Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'msyh.ttc',
                  Path('/System/Library/Fonts/PingFang.ttc'),
                  Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')]
    local_font = next((candidate for candidate in font_paths if candidate.is_file()),None)
    if local_font:
        font = ImageFont.truetype(str(local_font),14)
        labels = ['银球 · 红色参照','银球 · 蓝色参照','玻璃 · 折射率 1.52','玻璃 · 折射率 1.001']
    else:
        font = ImageFont.load_default()
        labels = ['Silver | red reference','Silver | blue reference','Glass | IOR 1.52','Glass | IOR 1.001']
    views = [(base,mirror),(blue,mirror),(base,glass),(air,glass)]
    cells = []
    for (frame, sphere), caption in zip(views,labels):
        cx, cy, r = [round(sphere[k]) for k in ('x', 'y', 'radius')]
        crop = Image.fromarray(frame).crop((cx-r-4, cy-r-4, cx+r+4, cy+r+4))
        cells.append((crop, caption))
    width = max(crop.width for crop, _ in cells)
    height = max(crop.height for crop, _ in cells)
    cell_h = height+32
    sheet = Image.new('RGB', (width*2+36, cell_h*2+24), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, (crop, caption) in enumerate(cells):
        x, y = 12+(i%2)*(width+12), 8+(i//2)*cell_h
        draw.text((x, y), caption, font=font, fill='#192630')
        sheet.paste(crop, (x, y+20))
    sheet.save(path)


def frame_sample(page, sphere, category, output, samples):
    page.locator('#localMaterialCategory').select_option(category)
    page.evaluate("""({center,width}) => {
      const v=realtime.instance, bounds=v.bounds;
      const diff=center.map((x,i)=>(x-bounds.center[i])/bounds.diagonal);
      const shot={...v.snapshot(),ortho_scale:width,target_offset:[diff[0],-diff[2],diff[1]]};
      v.setShot(shot,v.initialPlan);
    }""", {'center':sphere['world'],'width':2.9 if category=='Glass' else 3.35})
    traced_frame(page,samples)
    page.mouse.move(2,2)
    page.screenshot(path=str(output))


def verify_material_cancel(page, sphere):
    ref = page.locator('#realtimeViewport .realtime-canvas-frame').bounding_box()
    x, y = ref['x']+sphere['x'], ref['y']+sphere['y']
    page.locator('#localMaterialCategory').select_option('Glass')
    card = page.locator('#localMaterialGrid .local-material-tile').first.bounding_box()
    identity = """() => {
      const s=[...realtime.instance.librarySlots.values()].find(s=>s.object_name==='Glass sphere');
      const {node,index}=s.bindings[0];
      return [current.version,(Array.isArray(node.material)?node.material[index]:node.material).uuid];
    }"""
    original = page.evaluate(identity)
    page.mouse.move(card['x']+card['width']/2,card['y']+card['height']/2)
    page.mouse.down()
    page.mouse.move(card['x']+6,card['y']+6,steps=4)
    page.mouse.move(x,y,steps=12)
    page.mouse.move(x+1,y+1)
    page.wait_for_function("""() => {
      const v=realtime.instance;
      return v.previewTarget?.object_name==='Glass sphere' && !v.pathTracing.hasFrame &&
        v.frame.dataset.traceMode==='raster' && v.frame.dataset.traceSamples==='0';
    }""")
    hover = page.evaluate(identity)
    assert hover[0]==original[0] and hover[1]!=original[1]
    page.keyboard.press('Escape')
    page.mouse.up()
    page.wait_for_function("!realtime.instance.previewTarget")
    assert page.evaluate(identity)==original
    traced_frame(page,4)


def run(args):
    from playwright.sync_api import sync_playwright
    args.output.mkdir(parents=True, exist_ok=True)
    paths=[os.environ.get('WUYING_TEST_CHROME'),shutil.which('google-chrome'),shutil.which('chromium'),
           r'C:\Program Files\Google\Chrome\Application\chrome.exe',
           r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
    chrome=next((p for p in paths if p and Path(p).is_file()),None)
    adapter=['--use-angle=swiftshader','--enable-unsafe-swiftshader'] if args.software else (
        ['--use-angle=d3d11','--enable-gpu'] if os.name=='nt' else ['--enable-gpu'])
    with project_server(backdrop='white') as (url,ids), sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=chrome,headless=True,args=adapter,timeout=30000)
        try:
            page=browser.new_page(viewport={'width':1320,'height':800},device_scale_factor=1)
            page.set_default_timeout(60000)
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            page.on('console',lambda msg:errors.append(msg.text) if 'Shader Error' in msg.text else None)
            page.goto(url)
            cold_start=time.perf_counter()
            open_project(page,ids['base'])
            live_seconds=time.perf_counter()-cold_start
            mirror=sphere_geometry(page,'Silver sphere')
            glass=sphere_geometry(page,'Glass sphere')
            adapter_name=page.evaluate("""() => {
              const gl=realtime.instance.renderer.getContext(),e=gl.getExtension('WEBGL_debug_renderer_info');
              return gl.getParameter(e?e.UNMASKED_RENDERER_WEBGL:gl.RENDERER);
            }""")
            print('Optics test adapter:',adapter_name,flush=True)
            page.evaluate("limit => realtime.instance.setTraceOptions({enabled:true,limit})",args.samples)
            page.wait_for_function("""() => {
              const d=realtime.instance.frame.dataset;
              return d.traceMode==='fallback' || (d.traceMode==='pathtracing' && Number(d.traceSamples)>=1);
            }""",timeout=180000)
            first_seconds=time.perf_counter()-cold_start
            first=page.evaluate("() => realtime.instance.pathTracing.state")
            if args.software:
                assert first['mode']=='fallback' and first['phase']=='unavailable',first
                assert first['adapterType']=='software' and 'NVIDIA' in first['reason'],first
                assert 'swiftshader' in first['adapterName'].lower(),first
                canvas=page.locator('#realtimeViewport canvas').first
                bounds=canvas.bounding_box()
                before=page.evaluate('realtime.instance.camera.position.toArray()')
                x=bounds['x']+bounds['width']*.5
                y=bounds['y']+bounds['height']*.5
                page.mouse.move(x,y)
                page.mouse.down()
                page.mouse.move(x+40,y+10,steps=5)
                page.mouse.up()
                page.wait_for_function('start => realtime.instance.camera.position.toArray().some((n,i)=>Math.abs(n-start[i])>1e-4)',arg=before)
                assert page.evaluate('realtime.instance.pathTracing.worker===null && !realtime.instance.pathTracing.hasFrame && realtime.instance.pathTracing.state.samples===0')
                assert not errors,errors
                print('Software fallback: camera orbit works, no path samples,',first['adapterName'],flush=True)
                return
            assert first['mode']=='pathtracing', first
            print(f'Cold project open: live model {live_seconds:.2f}s, first traced frame {first_seconds:.2f}s',flush=True)
            base=traced_frame(page,args.samples)
            white_fraction=white_backdrop(base)
            shadow=floor_shadow(page,base,mirror)
            worker_state=page.evaluate("() => realtime.instance.pathTracing.state")
            print('Path tracing worker:',worker_state,flush=True)
            frame_sample(page,mirror,'Metal',args.output/'pathtrace-metal-main.png',args.samples)
            # Both screen-space crops use the same physical source and camera direction.
            frame_sample(page,glass,'Glass',args.output/'pathtrace-glass-main.png',args.samples)
            open_project(page,ids['blue'])
            blue=traced_frame(page,args.samples)
            metal_result=inspect_metal(base,blue,mirror)
            print('Mirror geometry:',metal_result,flush=True)
            open_project(page,ids['air'])
            air=traced_frame(page,args.samples)
            glass_result=inspect_glass(base,air,glass)
            print('Glass refraction:',glass_result,flush=True)
            secondary=reflected_glass(base,air,mirror)
            print('Silver reflection of glass:',secondary,flush=True)
            write_comparison(args.output/'pathtrace-optics-comparison.png',base,blue,air,mirror,glass)
            verify_material_cancel(page,glass)
            assert not errors,errors
            report={'renderer':adapter_name,'worker_adapter':worker_state['adapterType'],'worker_renderer':worker_state['adapterName'],'samples':args.samples,
                    'white_backdrop_fraction':white_fraction,
                    'floor_shadow':shadow,'cold_project_to_raster_seconds':round(live_seconds,3),
                    'cold_project_to_first_traced_seconds':round(first_seconds,3),
                    'mirror':metal_result,'glass':glass_result,'glass_in_mirror':secondary}
            (args.output/'pathtrace-optics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'validation')
    parser.add_argument('--samples',type=int,default=16)
    parser.add_argument('--software',action='store_true')
    run(parser.parse_args())

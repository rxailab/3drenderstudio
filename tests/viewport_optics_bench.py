"""Measure the interactive viewport and 128-sample trace against a saved Cycles export.

Create the reference using viewport_final_render_smoke.py. Pass --old-modules
with a snapshot of viewport modules for a controlled before/after comparison.
"""
import argparse
import base64
import io
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import disc, open_project, project_server, sphere_geometry
from pathtrace_orbit_bench import measure
from studio_backdrop_picker_smoke import choose
from viewport_final_render_smoke import compare
from studio_backdrops import catalog as backdrop_catalog


ROOT = Path(__file__).resolve().parents[1]


def capture(page, size):
    data = page.evaluate("realtime.instance.captureMaterialFrame().toDataURL().split(',')[1]")
    return Image.open(io.BytesIO(base64.b64decode(data))).convert('RGB').resize(size, Image.Resampling.LANCZOS)


def feature_errors(frame, final, regions):
    # Silver highlight intensity and position, inside the sphere only. Use the
    # same reference threshold in both images so a dimmed highlight is visible.
    frames = [np.asarray(im, dtype=np.float32) for im in (frame, final.resize(frame.size, Image.Resampling.LANCZOS))]
    lumas = [np.dot(im,[.2126,.7152,.0722]).astype(np.float32) for im in frames]
    silver = disc(lumas[0],regions['silver'])
    threshold = np.quantile(lumas[1][silver],.85)
    masks = [silver & (gray>=threshold) for gray in lumas]
    overlap = (masks[0]&masks[1]).sum() / (masks[0]|masks[1]).sum()
    intensity = np.abs(lumas[0][masks[1]]-lumas[1][masks[1]]).mean()
    # Isolate the upper softbox reflections from the large bright ground
    # reflection. Use a near-white display level for isolated light sources.
    upper = silver & (np.arange(silver.shape[0])[:,None] < regions['silver']['y'])
    lights = [upper & (gray >= .95*255) for gray in lumas]
    any_light = lights[0]|lights[1]
    # Distance between prominent refracted contours within the glass. Smooth
    # subpixel sampling noise before detecting edges. Measure both directions.
    inside = disc(lumas[0],regions['glass'])
    edges = [(cv2.Canny(cv2.GaussianBlur(np.uint8(gray),(5,5),1.1),28,60)>0)&inside for gray in lumas]
    assert all(e.any() for e in edges), 'Glass contours are missing'
    distances = [cv2.distanceTransform(np.uint8(~edges[1-i]),cv2.DIST_L2,5)[e] for i,e in enumerate(edges)]
    return dict(silver_highlight_iou=round(float(overlap),3),
                silver_highlight_mae=round(float(intensity),3),
                silver_softbox_iou=round(float((lights[0]&lights[1]).sum()/any_light.sum()),3) if any_light.any() else None,
                glass_edge_mean_distance_px=round(float(np.mean([d.mean() for d in distances])),3))


def run(args):
    ref = json.loads((args.reference/'comparison.json').read_text(encoding='utf8'))
    final = Image.open(args.reference/'cycles.png').convert('RGB')
    preset = backdrop_catalog()[ref['preset']]
    camera = ref['viewport_geometry']['camera']
    args.output.mkdir(parents=True,exist_ok=True)
    with project_server() as (url,ids):
        from app import app
        from flask import request, send_file
        from studio_store import get, put
        if args.old_modules:
            # This also serves nested module requests made by the GPU worker.
            replacements = {('/static/vendor/pathtracer/' if p.name=='webgl-pathtracer.js' else '/static/')+p.name:p.resolve()
                            for p in args.old_modules.iterdir() if p.is_file()}

            @app.before_request
            def serve_snapshot():
                if request.path in replacements:
                    return send_file(replacements[request.path],mimetype='text/javascript')

        p = get('project',ids['base'])
        p['plan'].update(aspect='4:3',visual_review=False,auto_polish=False)
        p['plan']['shots'][0].update(camera)
        put('project',p)
        with sync_playwright() as pw, pw.chromium.launch(channel='chrome',headless=True,
                args=['--use-angle=d3d11','--enable-gpu']) as browser:
            page = browser.new_page(viewport={'width':1470,'height':860},device_scale_factor=1)
            page.set_default_timeout(90000)
            page.add_init_script("localStorage.setItem('wuying-viewport-trace','off')")
            errors = []
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('console',lambda m:errors.append(m.text) if 'Shader Error' in m.text else None)
            page.goto(url)
            open_project(page,ids['base'])
            choose(page,preset)
            page.wait_for_function("realtime.instance.frame.dataset.previewOptics==='ready'")
            box = page.locator('#realtimeViewport .realtime-canvas-frame').bounding_box()
            size = tuple(round(box[k]) for k in ('width','height'))
            regions = {name:sphere_geometry(page,title) for name,title in [('glass','Glass sphere'),('silver','Silver sphere')]}
            observed = page.evaluate('realtime.instance.snapshot()')
            for key in ['azimuth','elevation','ortho_scale']:
                assert abs(observed[key]-camera[key])<.002,(observed,camera)
            # Hold a real orbit at exactly the reference pose for the fast pass.
            page.mouse.move(box['x']+box['width']*.48,box['y']+box['height']*.55)
            page.mouse.down()
            page.wait_for_function("realtime.instance.orbiting && !!realtime.instance.motionQuality")
            fast = capture(page,size)
            page.mouse.up()
            fast.save(args.output/'interactive.png')
            assert not errors,errors
            results = dict(reference=ref['preset'],size=size,camera=observed,regions=regions,
                           interactive={**compare(fast,final,preset,regions,[]),**feature_errors(fast,final,regions)},traces=[])
            if args.interactive_only:
                (args.output/'metrics.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf8')
                print(results['interactive'],flush=True)
                return
            page.evaluate("""() => {
                const t=realtime.instance.pathTracing, show=t.showFrame;
                t.showFrame=function(bitmap,samples) {
                    show.call(this,bitmap,samples);
                    window.submittedFrames.push([samples, Math.round(performance.now()-window.traceStart)]);
                };
            }""")
            for i in range(1+args.warm_runs):
                page.evaluate("""first => {
                    window.submittedFrames=[]; window.traceStart=performance.now();
                    if(first)realtime.instance.setTraceOptions({enabled:true,limit:128});
                    else realtime.instance.requestDraw();
                }""",i==0)
                page.wait_for_function("submittedFrames.at(-1)?.[0]===128 || realtime.instance.pathTracing.state.mode==='fallback'",timeout=240000)
                state = page.evaluate('realtime.instance.pathTracing.state')
                assert state['samples']==128 and 'NVIDIA' in state['adapterName'],state
                frames = page.evaluate('submittedFrames')
                assert frames[0][0]==1 and all(a[0]<b[0] for a,b in zip(frames,frames[1:])),frames
                frame = capture(page,size)
                optical = {**compare(frame,final,preset,regions,[]),**feature_errors(frame,final,regions)}
                results['traces'].append(dict(cold=i==0,first_ms=frames[0][1],complete_ms=frames[-1][1],
                                              updates=len(frames),milestones=frames,optical=optical))
                print('Trace',i+1,'first / complete (ms), bitmaps',frames[0][1],frames[-1][1],len(frames),flush=True)
            frame.save(args.output/'viewport.png')
            results.update(adapter=state['adapterName'],trace=optical)
            page.evaluate('() => { window.submittedFrames=[]; realtime.instance.requestDraw(); }')
            page.wait_for_function("submittedFrames.length>=2",timeout=120000)
            results['drag'] = measure(page)
            page.wait_for_function("Number(realtime.instance.frame.dataset.traceSamples)>=1",timeout=90000)
            results['drag']['release_to_trace_ms']=round(page.evaluate('performance.now()-orbitProbe.released'))
            assert not errors,errors
            (args.output/'metrics.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf8')
            print('Optical comparison',results['trace'],'drag',results['drag'],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,default=ROOT/'validation/viewport-cycles-4k')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--old-modules',type=Path)
    parser.add_argument('--warm-runs',type=int,default=2)
    parser.add_argument('--interactive-only',action='store_true')
    run(parser.parse_args())

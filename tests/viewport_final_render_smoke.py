"""Compare a real main-view GPU frame with the final, full-resolution downloadable PNG.

Run from the project folder: python tests/viewport_final_render_smoke.py
Use --preview for a faster Blender-preflight diagnostic.
"""
import argparse
import base64
import io
import json
import time
from pathlib import Path
from urllib.request import urlopen

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import disc, open_project, project_server, sphere_geometry
from studio_backdrop_picker_smoke import choose
from studio_backdrops import catalog as backdrop_catalog, default_backdrop


ROOT = Path(__file__).resolve().parents[1]


def read_url(base, path):
    with urlopen(base.rstrip('/') + path, timeout=20) as response:
        return response.read()


def compare(live_image, exported, preset, regions, floor_points):
    live = np.asarray(live_image, dtype=np.int16)
    final = np.asarray(exported.resize(live_image.size, Image.Resampling.LANCZOS), dtype=np.int16)
    h, w = live.shape[:2]
    original=np.full((h,w,3),255,dtype=np.int16)
    if preset['image']:
        with Image.open(ROOT / preset['image'].lstrip('/')) as photo:
            ratio = max(w/photo.width, h/photo.height)
            plate = photo.convert('RGB').resize((round(photo.width*ratio), round(photo.height*ratio)), Image.Resampling.LANCZOS)
            x = (plate.width-w)//2
            y = (plate.height-h)//2
            original = np.asarray(plate.crop((x,y,x+w,y+h)), dtype=np.int16)

    # An asymmetric photograph permits detection of exposure changes, even a
    # small crop or a horizontal flip. No model hits any of these rows.
    sky = np.s_[round(h*.055):round(h*.20), round(w*.045):round(w*.955)]
    def mae(a, b):
        return round(float(np.abs(a-b).mean()), 3)
    results = {
        'live_background_vs_plate_mae': mae(live[sky], original[sky]),
        'export_background_vs_plate_mae': mae(final[sky], original[sky]),
        'export_background_vs_live_mae': mae(final[sky], live[sky]),
    }
    # The matte red upright target has a directly visible contour. Its
    # centroid and dimensions detect a moved camera independently of shading.
    def marker(image):
        red,green,blue = image.transpose(2,0,1)
        select = ((red > green*1.7) & (red > blue*1.55) & (red>95)).astype(np.uint8)
        count,_,stats,centers = cv2.connectedComponentsWithStats(select)
        assert count>1, 'Red target is missing'
        i = 1 + np.argmax(stats[1:,cv2.CC_STAT_AREA])
        return centers[i], stats[i,2:4]
    center_live, size_live = marker(live)
    center_final, size_final = marker(final)
    results['red_target_center_displacement_px'] = round(float(np.linalg.norm(center_live-center_final)), 2)
    results['red_target_size_ratio'] = np.round(size_final / size_live, 3).tolist()
    for name, sphere in regions.items():
        mask = disc(live,sphere)
        results[name+'_mae'] = mae(final[mask],live[mask])
        la = live[mask].astype(float).mean(axis=1)
        lb = final[mask].astype(float).mean(axis=1)
        results[name+'_brightness_correlation'] = round(float(np.corrcoef(la,lb)[0,1]),3)
    if floor_points:
        px,py=np.asarray(floor_points).T
        plate_luma=original[py,px].mean(axis=1)
        live_drop=plate_luma-live[py,px].mean(axis=1)
        final_drop=plate_luma-final[py,px].mean(axis=1)
        receiver=live_drop>6
        if receiver.any():
            results['floor_shadow_test_points']=int(receiver.sum())
            results['live_shadow_mean_luma_drop']=round(float(live_drop[receiver].mean()),2)
            results['export_shadow_mean_luma_drop_at_same_points']=round(float(final_drop[receiver].mean()),2)
    return results


def run(preview, preset_id):
    kind = 'preview' if preview else 'render'
    preset = backdrop_catalog()[preset_id]
    output = ROOT/'validation'/('viewport-cycles-preview' if preview else 'viewport-cycles-4k')
    if preset_id!='cologne':
        output=output/preset_id
    output.mkdir(parents=True, exist_ok=True)
    with project_server() as (url, ids):
        import engine
        from studio_store import get, put
        project = get('project', ids['base'])
        project['plan'].update(aspect='4:3',visual_review=False,auto_polish=False)
        put('project',project)
        engine.start()
        with sync_playwright() as pw, pw.chromium.launch(
            channel='chrome',headless=True,args=['--use-angle=d3d11','--enable-gpu']
        ) as browser:
            page = browser.new_page(viewport={'width':1470,'height':860},device_scale_factor=1)
            page.set_default_timeout(90000)
            page.on('pageerror',lambda error:print('Browser error:',error,flush=True))
            page.on('response',lambda response:print('Service error:',response.status,response.url,
                    response.text()[:500],flush=True) if response.status>=400 else None)
            page.goto(url)
            open_project(page,ids['base'])
            choose(page,preset)
            # Exercise the interactive orbit; the final-render button must
            # capture the current framing without making users apply it twice.
            canvas = page.locator('#realtimeViewport .realtime-canvas-frame')
            box = canvas.bounding_box()
            cx, cy = box['x'] + box['width']*.62, box['y'] + box['height']*.43
            page.mouse.move(cx,cy)
            page.mouse.down()
            page.mouse.move(cx+12,cy+4,steps=6)
            page.mouse.up()
            samples = 8 if preview else 128
            page.evaluate("n => realtime.instance.setTraceOptions({enabled:true,limit:n})",samples)
            page.wait_for_function("""n => {
                const s = realtime.instance.pathTracing.state;
                return s.mode==='fallback' || (s.mode==='pathtracing' && s.samples>=n);
            }""",arg=samples,timeout=240000)
            state = page.evaluate('realtime.instance.pathTracing.state')
            assert state['samples']>=samples and 'NVIDIA' in state['adapterName'],state
            print('Viewport reached',state['samples'],'samples on',state['adapterName'],flush=True)
            view_geometry = page.evaluate("""() => {
                const v = realtime.instance;
                return {camera:v.snapshot(),bounds:v.bounds};
            }""")
            encoded = page.evaluate("realtime.instance.captureMaterialFrame().toDataURL('image/png').split(',')[1]")
            frame = Image.open(io.BytesIO(base64.b64decode(encoded))).convert('RGB')
            frame = frame.resize((round(box['width']),round(box['height'])),Image.Resampling.LANCZOS)
            frame.save(output/'viewport.png')
            regions = {name:sphere_geometry(page,title) for name,title in [('glass','Glass sphere'),('silver','Silver sphere')]}
            floor_points=page.evaluate("""() => {
                const v=realtime.instance, r=v.frame.getBoundingClientRect(), pts=[];
                for(let dy=.54;dy<.91;dy+=.012)for(let dx=.06;dx<.95;dx+=.016) {
                    const x=Math.round(dx*r.width), y=Math.round(dy*r.height);
                    if(!v.materialTargetAt(r.x+x,r.y+y))pts.push([x,y]);
                }
                return pts;
            }""")
            try:
                with page.expect_response(lambda res: res.request.method=='POST' and res.url.endswith('/jobs'),timeout=35000) as queued:
                    page.locator('#previewButton' if preview else '#renderButton').click()
                    if not preview:
                        page.locator('#renderConfirmDialog [data-choice="render"]').click()
            except Exception:
                print('Render controls:',page.evaluate("""() => ({
                    toast:$('toast').textContent,dirty,busy,disabled:$('renderButton').disabled,
                    version:current.version,selection:studioBackdropUI?.selection,
                    dialogs:[...document.querySelectorAll('dialog[open]')].map(d=>d.textContent)
                })"""),flush=True)
                raise
            job_response = queued.value
            assert job_response.status==202,job_response.text()
            job = job_response.json()
            assert job['kind']==kind and job['shot_ids']==['hero'],job
            assert job['plan'].get('viewport_backdrop')==preset['id'],job['plan'].get('viewport_backdrop')
            shot=next(s for s in job['plan']['shots'] if s['id']=='hero')
            for key in ('azimuth','elevation','ortho_scale','target_offset'):
                np.testing.assert_allclose(shot[key],view_geometry['camera'][key],rtol=1e-6,atol=1e-5,
                                           err_msg='Submitted camera differs from displayed camera: '+key)
            print('Main view submitted',kind,job['id'],'snapshot',job['plan']['viewport_backdrop'],flush=True)
        start = time.monotonic()
        progress = None
        while time.monotonic()-start<1200:
            job = json.loads(read_url(url,'/api/jobs/'+job['id']))
            current = job.get('stage'),job.get('progress')
            if current!=progress:
                print(*current,flush=True)
                progress = current
            if job['status'] in ('completed','failed','interrupted','cancelled'):
                break
            time.sleep(3)
        assert job['status']=='completed',job.get('error',job['status'])
        project=json.loads(read_url(url,'/api/projects/'+ids['base']))
        record=next(o for o in project['outputs'] if o['job_id']==job['id'])
        result=json.loads(read_url(url,record['result_url']))
        png=read_url(url,record['png_url'])
        (output/'cycles.png').write_bytes(png)
        with Image.open(io.BytesIO(png)) as raw:
            final=raw.convert('RGB')
        assert final.size==((512,384) if preview else (4096,3072)),final.size
        assert result['device']['type']=='GPU' and result['device']['backend'] in ('OPTIX','CUDA'),result['device']
        assert result['render_settings']['engine']=='CYCLES',result['render_settings']
        assert record.get('viewport_backdrop')==result.get('viewport_backdrop')==preset['id']
        if not preview:
            assert result['render_settings']['samples']==128,result['render_settings']
        metrics=compare(frame,final,preset,regions,floor_points)
        report=dict(preset=preset['id'],source=state['adapterName'],viewport_samples=state['samples'],
                    size=final.size,device=result['device'],cycles=result['render_settings'],comparison=metrics,
                    render_seconds=result.get('render_seconds'),
                    viewport_geometry=view_geometry,export_camera=result.get('studio',{}).get('camera'))
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        (output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        small=(600,450)
        display=Image.new('RGB',(small[0]*2,small[1]+41),'#f5f4f0')
        draw=ImageDraw.Draw(display)
        font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',17)
        for i,(im,label) in enumerate(((frame,'主视图 · NVIDIA 光追'),(final,'Cycles · %d × %d'%final.size))):
            display.paste(im.resize(small,Image.Resampling.LANCZOS),(i*small[0],0))
            draw.text((i*small[0]+16,small[1]+8),label,fill='#25272b',font=font)
        display.save(output/'comparison.jpg',quality=94)
        assert metrics['live_background_vs_plate_mae']<5,metrics
        assert metrics['export_background_vs_plate_mae']<5,metrics
        assert metrics['red_target_center_displacement_px']<max(3,frame.width*.007),metrics
        assert all(.90<v<1.10 for v in metrics['red_target_size_ratio']),metrics
        assert metrics['glass_brightness_correlation']>.48,metrics
        assert metrics['silver_brightness_correlation']>.38,metrics
        assert metrics['glass_mae'] < (60 if preview else 36),metrics
        assert metrics['silver_mae'] < (45 if preview else 25),metrics


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',action='store_true')
    parser.add_argument('--preset',default='cologne' if 'cologne' in backdrop_catalog() else default_backdrop(),help='Catalog photo/backdrop to compare')
    args=parser.parse_args()
    run(args.preview,args.preset)

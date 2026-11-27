"""Exercise live exposure, a retained viewport manifest and real Cycles previews.

The isolated project deliberately retains a 0 EV studio manifest after its plan
has been saved at +1 EV. Both the reopened viewport and the rendered image must
follow the current plan. No user project is modified.
"""
import argparse
import io
import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import disc, open_project, project_server, sphere_geometry
from studio_backdrop_picker_smoke import choose
from viewport_final_render_smoke import read_url
from viewport_optics_bench import ROOT, capture
from studio_backdrops import catalog as backdrop_catalog


def live(page,size):
    page.wait_for_function("""() => realtime.instance?.pathTracing?.state.samples===128 ||
                              realtime.instance?.pathTracing?.state.mode==='fallback'""",timeout=180000)
    state=page.evaluate('realtime.instance.pathTracing.state')
    assert state['samples']==128 and 'NVIDIA' in state['adapterName'],state
    return capture(page,size)


def render(page,url,ident,ev,camera,preset,final_4k=False):
    with page.expect_response(lambda res:res.request.method=='POST' and res.url.endswith('/jobs'),timeout=45000) as submitted:
        page.locator('#renderButton' if final_4k else '#previewButton').click()
        if final_4k:page.locator('#renderConfirmDialog [data-choice="render"]').click()
    response=submitted.value
    assert response.status==202,response.text()
    job=response.json()
    assert job['kind']==('render' if final_4k else 'preview') and job['shot_ids']==['hero'] and job['plan']['exposure']==ev,job
    assert job['plan']['viewport_backdrop']==preset['id']
    shot=next(s for s in job['plan']['shots'] if s['id']=='hero')
    deadline=time.monotonic()+420
    while time.monotonic()<deadline:
        job=json.loads(read_url(url,'/api/jobs/'+job['id']))
        if job['status'] in ('completed','failed','interrupted','cancelled'):
            break
        time.sleep(1)
    assert job['status']=='completed',job
    for key in ('azimuth','elevation','target_offset'):
        np.testing.assert_allclose(shot[key],camera[key],rtol=1e-5,atol=1e-5)
    # Viewport exports from a saved render can use normalized scene units.
    np.testing.assert_allclose(shot['ortho_scale']/shot['viewport_reference_diagonal'],
                               camera['ortho_scale']/camera['viewport_reference_diagonal'],rtol=1e-4)
    project=json.loads(read_url(url,'/api/projects/'+ident))
    output=next(o for o in project['outputs'] if o['job_id']==job['id'])
    result=json.loads(read_url(url,output['result_url']))
    assert result['device']['type']=='GPU' and result['render_settings']['engine']=='CYCLES',result['device']
    assert result['studio']['exposure']==ev,result['studio']
    image=Image.open(io.BytesIO(read_url(url,output['png_url']))).convert('RGB')
    expected_size=(4096,3072) if final_4k else (512,384)
    assert image.size==expected_size,image.size
    if final_4k:assert result['render_settings']['samples']==128 and result['device']['backend']=='OPTIX',result
    print('Cycles completed',ev,'EV',flush=True)
    return image,job['id'],job.get('viewport_studio_bases',{}).get('hero',{}).get('exposure')


def compare(images,regions):
    rgb={name:np.asarray(im,dtype=np.float32) for name,im in images.items()}
    luma={name:values @ [.2126,.7152,.0722] for name,values in rgb.items()}
    soft=[disc(luma['cycles0'],sphere) for sphere in regions.values()]
    midtones=(luma['cycles0']>45)&(luma['cycles0']<192)
    r,g,b=rgb['cycles0'].transpose(2,0,1)
    marker=cv2.erode(np.uint8((r>g*1.65)&(r>b*1.65)&(r>105)),np.ones((5,5),np.uint8)).astype(bool)
    masks={'silver':soft[0]&midtones,'glass':soft[1]&midtones,'red_card':marker}
    report={}
    for name,mask in masks.items():
        assert np.count_nonzero(mask)>80,(name,np.count_nonzero(mask))
        levels={k:round(float(v[mask].mean()),2) for k,v in luma.items()}
        delta=levels['cycles1']-levels['cycles0']
        draft_delta=levels['draft1']-levels['live0']
        reloaded_delta=levels['loaded1']-levels['live0']
        report[name]=dict(mean_luma=levels,cycles_change=round(delta,2),
                          draft_change=round(draft_delta,2),reopened_change=round(reloaded_delta,2))
    sky=np.s_[15:58,16:-16]
    report['background_mean_rgb_error']={key:float(np.abs(rgb[key][sky]-rgb[base][sky]).mean())
        for key,base in [('draft1','live0'),('loaded1','live0'),('cycles1','cycles0'),('live0','cycles0')]}
    return report


def run(output,final_4k=False):
    output.mkdir(parents=True,exist_ok=True)
    catalog=backdrop_catalog()
    preset=catalog.get('cologne',catalog['silver-beam'])
    size=(512,384)
    with project_server() as (url,ids):
        import engine
        from studio_store import get, put, atomic_json
        from viewport_service import TASKS
        ident=ids['base']
        p=get('project',ident)
        p['plan'].update(aspect='4:3',visual_review=False,auto_polish=False,exposure=0)
        put('project',p)
        engine.start()
        with sync_playwright() as pw,pw.chromium.launch(channel='chrome',headless=True,
                args=['--use-angle=d3d11','--enable-gpu']) as browser:
            page=browser.new_page(viewport={'width':1470,'height':860},device_scale_factor=1)
            page.set_default_timeout(90000)
            page.goto(url)
            open_project(page,ident)
            choose(page,preset)
            images={'live0':live(page,size)}
            images['live0'].save(output/'live0.png')
            camera=page.evaluate('realtime.instance.snapshot()')
            box=page.locator('#realtimeViewport .realtime-canvas-frame').bounding_box()
            scale=size[0]/box['width']
            regions={}
            for title in ('Silver sphere','Glass sphere'):
                roi=sphere_geometry(page,title)
                regions[title]={k:roi[k]*scale for k in ('x','y','radius')}
            images['cycles0'],job_id,_=render(page,url,ident,0,camera,preset)
            images['cycles0'].save(output/'cycles0.png')
            page.wait_for_function('id=>current.outputs.some(o=>o.job_id===id)',arg=job_id)
            page.locator('#liveViewButton').click()
            page.wait_for_function("realtime.status==='ready' && realtime.instance && !activeJob()")
            page.locator('#inspectorPlanTab').click()
            slider=page.locator('#exposure')
            slider.focus()
            assert float(slider.input_value())==0
            for _ in range(10):
                slider.press('ArrowRight')
            assert float(slider.input_value())==1
            assert page.evaluate('dirty && current.plan.exposure===0'),'Must test the unsaved slider value'
            images['draft1']=live(page,size)
            images['draft1'].save(output/'draft1.png')
            page.locator('#savePlan').click()
            page.wait_for_function("""() => current.plan.exposure===1 && !dirty && realtime.status==='ready'
                                           && realtime.record?.version===current.version""")
            task_id=page.evaluate('realtime.record.id')
            task=TASKS[task_id]
            assert task['project_id']==ident and task['status']=='ready'
            # Simulate a saved studio exposure from a previous revision. Keep
            # geometry, lamp layout, fingerprints and the selected plate intact.
            path=Path(task['_folder'])/'manifest.json'
            manifest=json.loads(path.read_text(encoding='utf8'))
            manifest['exposure']=0
            atomic_json(path,manifest)
            page.goto(url)
            open_project(page,ident)
            assert page.evaluate('realtime.instance.metadata.exposure')==0
            images['loaded1']=live(page,size)
            images['loaded1'].save(output/'loaded1.png')
            restored_camera=page.evaluate('realtime.instance.snapshot()')
            # A saved Blender scene may normalize all source coordinates.
            # Compare camera scale relative to each export's model bounds.
            for key in ('azimuth','elevation','target_offset'):
                np.testing.assert_allclose(restored_camera[key],camera[key],atol=1e-4)
            np.testing.assert_allclose(restored_camera['ortho_scale']/restored_camera['viewport_reference_diagonal'],
                camera['ortho_scale']/camera['viewport_reference_diagonal'],rtol=1e-4)
            exported,_,submitted_base=render(page,url,ident,1,restored_camera,preset,final_4k)
            exported.save(output/'cycles1.png')
            images['cycles1']=exported.resize(size,Image.Resampling.LANCZOS)
            report=compare(images,regions)
            report['export_size']=exported.size
            report['submitted_base_exposure']=submitted_base
            (output/'comparison.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
            print(json.dumps(report,indent=2),flush=True)
            for key in ('glass','silver','red_card'):
                row=report[key]
                expected=row['cycles_change']
                assert expected>6,row
                for field in ('draft_change','reopened_change'):
                    assert row[field]>6 and abs(row[field]-expected)<max(8,expected*.28),row
            assert max(report['background_mean_rgb_error'].values())<3,report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'validation/viewport-exposure')
    parser.add_argument('--final-4k',action='store_true',help='Use the real 128-sample 4K export for the +1 EV comparison')
    args=parser.parse_args()
    run(args.output,args.final_4k)

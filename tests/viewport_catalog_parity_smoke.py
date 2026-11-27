"""Compare the actual saved Cycles frame with its read-only GLB and catalog overrides."""
import argparse
import base64
import hashlib
import io
import json
import logging
from pathlib import Path
from threading import Thread

import cv2
import numpy as np
from flask import Flask, jsonify, request, send_file
from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from pathtrace_large_scene_smoke import ROOT
from viewport_texture_channels_smoke import frames
from studio_backdrops import catalog as backdrop_catalog


PAGE = """<!doctype html><meta charset=utf-8><link rel=stylesheet href=/static/styles.css>
<style>html,body{margin:0}#host{width:512px;height:512px}</style><div id=host></div>
<script type=importmap>{"imports":{"three":"/static/vendor/three/build/three.module.js"}}</script>
<script type=module>
import * as T from 'three';
import {ProductViewport} from '/static/viewport.js';
const {plan,metadata,materials,preset}=await (await fetch('/settings')).json();
const v=new ProductViewport(document.getElementById('host'),{onError:e=>{console.error(e);window.error=String(e)}});
v.setTraceOptions({enabled:false});
window.realtime={instance:v};
await v.load({url:'/files/fixture/viewport/export/scene.glb'},metadata,plan.shots.find(s=>s.id==='hero'),plan,'fixture');
v.applySavedLibraryMaterials(plan.object_material_overrides,materials);
await v.setStudioBackdrop(preset.id,preset);
window.handleMask=() => {
  const palette={};
  const names=plan.object_material_overrides.map(o=>o.object_name);
  const colors=[0xfe0000,0x00fe00,0x0000fe,0xfe00fe];
  const saved=[];
  v.model.traverse(node=>{
    if(!node.isMesh)return;
    const previous=node.material,slots=node.userData.librarySlotKeys || [];
    const masks=(Array.isArray(previous)?previous:[previous]).map((m,i)=>{
      const key=slots[i], obj=key?JSON.parse(key)[0]:'',c=colors[names.indexOf(obj)] || 0x000000;
      if(obj)palette[obj]=c;
      return new T.MeshBasicMaterial({color:c,toneMapped:false});
    });
    saved.push([node,previous,masks]);node.material=Array.isArray(previous)?masks:masks[0];
  });
  const image=document.createElement('canvas'),canvas=v.renderer.domElement;
  image.width=canvas.width;image.height=canvas.height;v.renderer.render(v.scene,v.camera);
  image.getContext('2d').drawImage(canvas,0,0);
  for(const [node,m,masks] of saved){node.material=m;for(const mat of masks)mat.dispose();}
  return {png:image.toDataURL(),palette};
};
v.requestDraw();
window.ready=true;
</script>"""


def blur(gray,sigma):
    return cv2.GaussianBlur(gray,(0,0),sigma)


def compare(raster,trace,cycles,mask):
    mask=cv2.erode(mask.astype('uint8'),np.ones((7,7),np.uint8)).astype(bool)
    assert mask.sum()>200
    ims=[np.asarray(im,dtype=np.float32) for im in (raster,trace,cycles)]
    lumas=[cv2.cvtColor(im,cv2.COLOR_RGB2GRAY) for im in ims]
    # High-band RMS captures short transverse ridges while rejecting broad
    # softbox reflections; the signed luminance mean is reported separately.
    bands=[blur(g,1.2)-blur(g,4) for g in lumas]
    ref=bands[-1][mask]
    stats={}
    for name,im,gray,band in zip(('raster','trace','cycles'),ims,lumas,bands):
        signal=band[mask]
        stats[name]=dict(detail_rms=round(float(np.sqrt(np.mean(signal**2))),3),
                         detail_rmse_vs_cycles=round(float(np.sqrt(np.mean((signal-ref)**2))),3),
                         detail_correlation=round(float(np.corrcoef(signal,ref)[0,1]),4),
                         luminance=round(float(gray[mask].mean()),2),
                         rgb_mae=round(float(np.abs(im[mask]-ims[-1][mask]).mean()),3))
    return stats


def handle_texture_band(images, mask):
    # Unroll the inside of the long grip into longitudinal scan lines. The
    # position follows the curved handle; a shadow on the background and the
    # outline cannot be counted as repeated knots or ridges.
    yy,xx=np.nonzero(mask)
    xy=np.column_stack((xx,yy))
    axis=np.linalg.eigh(np.cov(xy.T))[1][:,-1]
    if axis[0]<0:axis=-axis
    normal=np.array([-axis[1],axis[0]])
    along,across=xy@axis,xy@normal
    samples=np.arange(np.percentile(along,8),np.percentile(along,92),1.)
    edges=np.array([np.percentile(across[np.abs(along-x)<1.1],[5,95]) for x in samples])
    offsets=np.linspace(.2,.8,25)[:,None]
    span=edges[:,0]+offsets*(edges[:,1]-edges[:,0])
    x=(samples*axis[0]+span*normal[0]).astype('float32')
    y=(samples*axis[1]+span*normal[1]).astype('float32')
    frequency=np.fft.rfftfreq(len(samples))
    band=(frequency>=1/15)&(frequency<=1/5)
    samples_by_image={}
    for name,image in images.items():
        gray=cv2.cvtColor(np.asarray(image,dtype=np.float32),cv2.COLOR_RGB2GRAY)
        lines=cv2.remap(gray,x,y,cv2.INTER_LINEAR)
        lines-=lines.mean(axis=1,keepdims=True)
        window=np.hanning(lines.shape[1])
        transformed=np.fft.rfft(lines*window,axis=1)
        power=2*np.mean(np.abs(transformed[:,band])**2,axis=0)
        samples_by_image[name]=round(float(np.sqrt(power.sum()/(len(window)*(window**2).sum()))),3)
    return samples_by_image


def run(args):
    render_dir=args.render_dir.resolve()
    export=args.export.resolve()
    output=args.output
    output.mkdir(parents=True,exist_ok=True)
    source=json.loads((render_dir/'request.json').read_bytes())
    result=json.loads((render_dir/'result.json').read_bytes())
    metadata=json.loads((export/'manifest.json').read_bytes())
    plan=source['plan']
    assert result['render_settings']['engine']=='CYCLES' and result['viewport_backdrop']==plan['viewport_backdrop']
    assert hashlib.sha256((export/'scene.glb').read_bytes()).hexdigest()==metadata['asset_sha256']
    if metadata['source']['kind']=='saved_scene':
        assert hashlib.sha256(next(render_dir.glob('*.blend')).read_bytes()).hexdigest()==metadata['source']['sha256']
    shot=next(s for s in plan['shots'] if s['id']=='hero')
    assert abs(metadata['camera']['azimuth']-shot['azimuth'])<.001 and abs(metadata['camera']['elevation']-shot['elevation'])<.001
    preset=backdrop_catalog()[plan['viewport_backdrop']]
    import keyshot_catalog
    catalog=keyshot_catalog.catalog()['materials']
    lookup={item['id']:item for item in catalog}
    overrides=plan['object_material_overrides']
    app=Flask(__name__,static_folder=str(ROOT/'static'),static_url_path='/static')
    app.add_url_rule('/','main',lambda:PAGE)
    app.add_url_rule('/favicon.ico','ico',lambda:('',204))
    app.add_url_rule('/files/fixture/viewport/export/scene.glb','model',lambda:send_file(export/'scene.glb'))
    app.add_url_rule('/settings','settings',lambda:jsonify(plan=plan,metadata=metadata,preset=preset,
                         materials=[lookup[row['catalog_id']] for row in overrides]))
    app.add_url_rule('/api/material-library/<ident>/textures/<channel>','texture',
                     lambda ident,channel:send_file(keyshot_catalog.texture_path(ident,channel)))
    if args.old_modules:
        modules={('/static/vendor/pathtracer/' if p.name=='webgl-pathtracer.js' else '/static/')+p.name:p.resolve()
                 for p in args.old_modules.iterdir() if p.suffix in ('.js','.mjs')}
        @app.before_request
        def previous_modules():
            if request.path in modules:return send_file(modules[request.path],mimetype='text/javascript')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as pw,pw.chromium.launch(channel='chrome',headless=True,args=['--enable-gpu','--use-angle=d3d11']) as browser:
            page=browser.new_page(viewport={'width':800,'height':800},device_scale_factor=1)
            page.set_default_timeout(180000)
            errors=[]
            page.on('pageerror',lambda e:(errors.append(str(e)),print('Browser:',str(e),flush=True)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.wait_for_function('window.ready || window.error')
            raster,trace,state=frames(page,128)
            shot_seen=page.evaluate('realtime.instance.snapshot()')
            assert not errors and abs(shot_seen['azimuth']-shot['azimuth'])<.001
            labels=page.evaluate('handleMask()')
    finally:
        server.shutdown();thread.join();server.server_close()
    mask_image=Image.open(io.BytesIO(base64.b64decode(labels['png'].split(',')[1]))).convert('RGB')
    label=np.asarray(mask_image,dtype=np.int16)
    reference_image=Image.open(args.reference or next(render_dir.glob('*.png')))
    reference=reference_image.convert('RGB').resize(trace.size,Image.Resampling.LANCZOS)
    parts={}
    for row in overrides:
        obj=row['object_name'];color=labels['palette'][obj]
        rgb=[(color>>16)&255,(color>>8)&255,color & 255]
        mask=np.abs(label-rgb).max(axis=2)<=5
        if mask.sum()>500:
            parts[obj]=dict(material=lookup[row['catalog_id']]['name'],pixels=int(mask.sum()),
                            metrics=compare(raster,trace,reference,mask))
            if lookup[row['catalog_id']]['pbr'].get('procedural',{}).get('type')=='wood':
                parts[obj]['longitudinal_5_to_15px_rms']=handle_texture_band(
                    {'raster':raster,'trace':trace,'cycles':reference},mask)
    report=dict(source=metadata['source'],camera=shot_seen,lighting=preset['id'],samples=state['samples'],adapter=state['adapterName'],
                original_cycles_size=list(reference_image.size),tested_size=trace.size,parts=parts)
    for name,im in [('raster',raster),('trace',trace),('cycles',reference),('parts',mask_image)]:im.save(output/(name+'.png'))
    (output/'metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    if args.require_wood_match:
        wood=[p for p in parts.values() if 'longitudinal_5_to_15px_rms' in p]
        assert wood,'The camera must include a procedural wood grip'
        for part in wood:
            band=part['longitudinal_5_to_15px_rms']
            assert part['metrics']['trace']['detail_correlation']>.85,part
            assert band['trace']<=2*band['cycles']+.5,part


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render-dir',type=Path,required=True)
    parser.add_argument('--export',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--old-modules',type=Path)
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--require-wood-match',action='store_true',
                        help='Check wood-grain alignment and narrow stripe energy against Cycles')
    run(parser.parse_args())

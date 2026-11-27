"""Compare raster and NVIDIA path-traced material maps on a read-only GLB export."""
import argparse
import base64
import hashlib
import io
import json
import logging
from pathlib import Path
from threading import Thread

import numpy as np
from flask import Flask, request, send_file
from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from pathtrace_large_scene_smoke import PAGE, ROOT


def capture(page):
    url = page.evaluate("realtime.instance.captureMaterialFrame().toDataURL('image/png')")
    return Image.open(io.BytesIO(base64.b64decode(url.split(',')[1]))).convert('RGB')


def frames(page, samples):
    page.wait_for_function('realtime.instance.refinement.count > 0')
    raster = capture(page)
    page.evaluate('limit => realtime.instance.setTraceOptions({enabled:true,limit})', samples)
    page.wait_for_function("""n => realtime.instance.pathTracing.state.samples === n ||
                                  realtime.instance.pathTracing.state.mode==='fallback'""", arg=samples)
    state = page.evaluate('realtime.instance.pathTracing.state')
    assert state['samples'] == samples and 'NVIDIA' in state['adapterName'], state
    trace = capture(page)
    return raster, trace, state


def differences(raster, trace, mask):
    a, b = np.asarray(raster, dtype=np.float32), np.asarray(trace, dtype=np.float32)
    def red(image):
        r, g, b = image.transpose(2, 0, 1)
        return (r > 1.30 * g + 8) & (r > 1.25 * b + 8)
    ar, br = red(a) & mask, red(b) & mask
    return dict(pixels=int(mask.sum()), raster_red=int(ar.sum()), trace_red=int(br.sum()),
                red_iou=round(float((ar & br).sum() / max(1, (ar | br).sum())), 4),
                mean_abs_rgb=round(float(np.abs(a[mask] - b[mask]).mean()), 3))


def run(directory, output, modules=None, mixed_uv=False):
    scene = directory / 'scene.glb'
    manifest = directory / 'manifest.json'
    metadata = json.loads(manifest.read_text(encoding='utf8'))
    assert hashlib.sha256(scene.read_bytes()).hexdigest() == metadata['asset_sha256']
    app = Flask(__name__, static_folder=str(ROOT / 'static'), static_url_path='/static')
    page_html = PAGE.replace("metrics.start=performance.now();", "view.setTraceOptions({enabled:false}); metrics.start=performance.now();")
    app.add_url_rule('/', 'main', lambda: page_html)
    app.add_url_rule('/favicon.ico', 'ico', lambda: ('', 204))
    app.add_url_rule('/files/fixture/viewport/export/scene.glb', 'model', lambda: send_file(scene, mimetype='model/gltf-binary'))
    app.add_url_rule('/manifest.json', 'metadata', lambda: send_file(manifest, mimetype='application/json'))
    if modules:
        replacements = {('/static/vendor/pathtracer/' if p.name == 'webgl-pathtracer.js' else '/static/') + p.name:
                        p.resolve() for p in modules.glob('*.js')}
        @app.before_request
        def serve_older_module():
            if request.path in replacements:
                return send_file(replacements[request.path], mimetype='text/javascript')
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw, pw.chromium.launch(channel='chrome', headless=True,
                args=['--enable-gpu','--use-angle=d3d11']) as browser:
            page = browser.new_page(viewport={'width':800,'height':800}, device_scale_factor=1)
            page.set_default_timeout(210000)
            errors = []
            page.on('pageerror', lambda err: errors.append(str(err)))
            page.goto(f'http://127.0.0.1:{server.server_port}/')
            page.wait_for_function('window.bench?.loaded || window.bench?.errors.length')
            assert not page.evaluate('bench.errors')
            raster, trace, state = frames(page, 128)
            raster.save(output/'raster.png')
            trace.save(output/'trace-128.png')
            # Evaluate UV correspondence independently of different lighting models.
            # Show the same real base-color textures as unlit in both renderers, then
            # segment each part using a flat color pass on the original camera.
            channels = page.evaluate("""async mixed => {
              const T=await import('/static/vendor/three/build/three.module.js');
              const v=realtime.instance;
              v.setTraceOptions({enabled:false});
              const textures=new Map(), ids=new Map(), palette=[0xff0000,0x00ff00,0x0000ff,0xffff00,0xff00ff,0x00ffff];
              const display=[], channels=[];
              v.model.traverseVisible(mesh=>{
                if(!mesh.isMesh)return;
                const convert=m=>{
                  if(!textures.has(m)){
                    const index=ids.size;
                    // The mixed fixture puts the source metal appearance on UV0
                    // and the handles on UV1. Each material also carries a second
                    // map using the opposite channel. With black diffuse and an
                    // emissive map the rendered colors can be checked directly.
                    let unlit=new T.MeshBasicMaterial({map:m.map,color:m.color,side:m.side}), map=m.map;
                    if(mixed && map){
                      map=map.clone();
                      map.channel=m.name.includes('handles')?1:0;
                      const rough=map.clone();rough.channel=1-map.channel;
                      unlit=new T.MeshPhysicalMaterial({color:0,emissive:m.color,side:m.side,
                        emissiveMap:map,roughnessMap:rough,metalness:0,specularIntensity:0});
                    }
                    textures.set(m,unlit);
                    ids.set(m,new T.MeshBasicMaterial({color:palette[index],toneMapped:false,side:m.side}));
                    channels.push({name:m.name,channel:map?.channel??null,mask:palette[index]});
                  }
                };
                const materials=mesh.material;
                (Array.isArray(materials)?materials:[materials]).forEach(convert);
                display.push([mesh,materials]);
              });
              function set(map) { for(const [mesh,source] of display)
                mesh.material=Array.isArray(source)?source.map(m=>map.get(m)):map.get(source); }
              set(ids);
              v.renderer.render(v.scene,v.camera);
              const image=document.createElement('canvas'),canvas=v.renderer.domElement;
              image.width=canvas.width;image.height=canvas.height;
              image.getContext('2d').drawImage(canvas,0,0);
              window.partMask=image.toDataURL();
              set(textures);
              v.invalidateTraceScene();
              return channels;
            }""", mixed_uv)
            raw_mask = page.evaluate('partMask').split(',')[1]
            labels = np.asarray(Image.open(io.BytesIO(base64.b64decode(raw_mask))).convert('RGB'),dtype=np.int16)
            albedo_raster, albedo_trace, _ = frames(page, 16)
            albedo_raster.save(output/'albedo-raster.png')
            albedo_trace.save(output/'albedo-trace.png')
            parts = []
            full = np.zeros(labels.shape[:2], dtype=bool)
            for part in channels:
                color = np.array([(part['mask']>>16)&255,(part['mask']>>8)&255,part['mask']&255])
                mask = np.max(np.abs(labels-color),axis=2) < 16
                if not mask.any():
                    continue
                full |= mask
                parts.append(dict(name=part['name'], channel=part['channel'], beauty=differences(raster,trace,mask),
                                  albedo=differences(albedo_raster,albedo_trace,mask)))
            assert full.sum() > 1000 and not errors, (channels, errors)
            report=dict(asset=metadata['asset_sha256'], camera=metadata['camera'], adapter=state['adapterName'],
                        samples=state['samples'], parts=parts, beauty=differences(raster,trace,full),
                        albedo=differences(albedo_raster,albedo_trace,full))
            print(json.dumps(report,ensure_ascii=False),flush=True)
            (output/'metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            if not modules:
                for part in parts:
                    assert part['albedo']['mean_abs_rgb'] < 8, part
                if mixed_uv:
                    assert {p['channel'] for p in parts} >= {0,1}, parts
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--old-modules',type=Path)
    parser.add_argument('--mixed-uv',action='store_true')
    args=parser.parse_args()
    run(args.export.resolve(),args.output,args.old_modules,args.mixed_uv)

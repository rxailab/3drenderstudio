"""Exercise the production viewport on an existing exported scene.glb, read-only.

Pass a viewport export directory containing manifest.json and scene.glb.
"""
import argparse
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sys
import threading

import numpy as np
from flask import Flask, send_file
from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from pathtrace_orbit_bench import measure


ROOT = Path(__file__).resolve().parents[1]
PAGE = """<!doctype html><meta charset=utf-8>
<link rel="stylesheet" href="/static/styles.css">
<style>html,body {margin:0;background:white} #realtimeViewport {width:752px;height:752px}</style>
<script type="importmap">{"imports":{"three":"/static/vendor/three/build/three.module.js"}}</script>
<div id="realtimeViewport"></div>
<script type="module">
import {ProductViewport} from '/static/viewport.js';
const metrics = window.bench = {frames:[],errors:[]};
async function open() {
  const metadata = await fetch('/manifest.json').then(r=>r.json());
  const view = new ProductViewport(document.querySelector('#realtimeViewport'),{
    onError:error=>metrics.errors.push(error.message)
  });
  window.realtime={instance:view};
  view.renderer.domElement.addEventListener('pointerup',()=>{metrics.release=performance.now();},true);
  const path=view.pathTracing;
  const show=path.showFrame;
  path.showFrame=function(bitmap,...args) {
    const w=bitmap.width,h=bitmap.height;
    show.call(this,bitmap,...args);
    metrics.frames.push({t:performance.now(),epoch:this.epoch,width:w,height:h});
  };
  metrics.start=performance.now();
  await view.load({url:'/files/fixture/viewport/export/scene.glb'},metadata,metadata.camera,
    {aspect:'1:1',margin:.1},'fixture');
  metrics.loaded=performance.now();
}
open().catch(error=>metrics.errors.push(String(error)));
</script>"""


def run(directory):
    model = (directory / 'scene.glb').resolve()
    manifest = (directory / 'manifest.json').resolve()
    metadata = json.loads(manifest.read_text(encoding='utf-8'))
    identity = hashlib.sha256()
    with model.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            identity.update(chunk)
    sha = identity.hexdigest()
    assert sha == metadata['asset_sha256']
    app = Flask(__name__, static_folder=str(ROOT / 'static'), static_url_path='/static')
    app.add_url_rule('/', 'viewport', lambda: PAGE)
    app.add_url_rule('/favicon.ico','favicon',lambda:('',204))
    app.add_url_rule('/files/fixture/viewport/export/scene.glb', 'model',
                     lambda: send_file(model, mimetype='model/gltf-binary'))
    app.add_url_rule('/manifest.json', 'metadata', lambda: send_file(manifest, mimetype='application/json'))
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    http = make_server('127.0.0.1', 0, app, threaded=True)
    web = threading.Thread(target=http.serve_forever, daemon=True)
    web.start()
    try:
        print('Starting Chromium with a temporary browser profile on the local test server.',file=sys.stderr,flush=True)
        with sync_playwright() as pw:
            flags = ['--enable-gpu'] + (['--use-angle=d3d11'] if os.name == 'nt' else [])
            with pw.chromium.launch(channel='chrome', args=flags, headless=True) as browser:
                page = browser.new_page(viewport={'width':800,'height':800},device_scale_factor=1)
                page.set_default_timeout(180000)
                errors = []
                page.on('pageerror',lambda error: errors.append(str(error)))
                page.on('console',lambda msg: print('Chrome:',msg.text,file=sys.stderr,flush=True)
                        if msg.type=='error' else None)
                print('Loading verified GLB into the product viewport.',file=sys.stderr,flush=True)
                page.goto(f'http://127.0.0.1:{http.server_port}/')
                try:
                    page.wait_for_function("window.bench?.errors.length || window.bench?.frames.length || window.realtime?.instance?.pathTracing?.state.mode==='fallback'")
                except Exception:
                    print('Viewport before first frame:',page.evaluate(
                        '() => ({bench:window.bench,state:window.realtime?.instance?.pathTracing?.state})'),
                        'Page errors:',errors,file=sys.stderr,flush=True)
                    raise
                state = page.evaluate('() => ({bench,state:window.realtime?.instance?.pathTracing.state})')
                assert not state['bench']['errors'] and state['bench']['frames'],state
                baseline = page.evaluate("""() => {
                  const v=realtime.instance,gl=v.renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');
                  let triangles=0,meshes=0;
                  v.model.traverseVisible(node=>{
                    if (!node.isMesh)return;
                    meshes++;
                    triangles+=(node.geometry.index?.count??node.geometry.attributes.position.count)/3;
                  });
                  return {adapter:gl.getParameter(ext?ext.UNMASKED_RENDERER_WEBGL:gl.RENDERER),
                    worker:v.pathTracing.adapterType,triangles,meshes,pose:v.snapshot()};
                }""")
                assert baseline['triangles']==metadata['geometry']['triangles'] and baseline['worker']=='hardware',baseline
                print('First path-traced frame reached the viewport. Dragging.',file=sys.stderr,flush=True)
                canvas = page.locator('#realtimeViewport canvas').first
                before = np.asarray(Image.open(io.BytesIO(canvas.screenshot())).convert('RGB'))
                orbit = measure(page)
                release = page.evaluate('bench.release')
                page.wait_for_function("""() => bench.errors.length ||
                  (bench.frames[bench.frames.length-1].epoch!==bench.frames[0].epoch &&
                    realtime.instance.pathTracing.state.mode==='pathtracing' && realtime.instance.pathTracing.state.samples>=2)
                  || realtime.instance.pathTracing.state.mode==='fallback'""")
                final = page.evaluate('() => ({bench, state:realtime.instance.pathTracing.state,pose:realtime.instance.snapshot()})')
                assert not final['bench']['errors'] and final['state']['mode']=='pathtracing',final['state']
                assert baseline['pose']!=final['pose']
                after = np.asarray(Image.open(io.BytesIO(canvas.screenshot())).convert('RGB'))
                difference = np.max(np.abs(before.astype(np.int16)-after.astype(np.int16)), axis=2)
                changed = float((difference>20).mean())
                assert changed>.01 and not errors,(changed,errors)
                events = final['bench']['frames']
                first = events[0]
                resumed = next(x for x in events if x['epoch']!=first['epoch'] and x['t']>=release)
                return dict(glb_sha256=sha,glb_bytes=model.stat().st_size,
                    viewport_export=str(directory),
                    renderer=baseline['adapter'],worker=baseline['worker'],visible_meshes=baseline['meshes'],
                    traced_triangles=baseline['triangles'],first_presented_frame_px=[first['width'],first['height']],
                    load_ms=round(final['bench']['loaded']-final['bench']['start'],1),
                    first_gpu_frame_from_load_start_ms=round(first['t']-final['bench']['start'],1),
                    first_gpu_frame_after_model_ready_ms=round(first['t']-final['bench']['loaded'],1),
                    resumed_gpu_frame_after_drag_ms=round(resumed['t']-release,1),
                    resumed_samples=final['state']['samples'],before_after_changed_fraction=round(changed,4),
                    orbit=orbit)
    finally:
        http.shutdown()
        web.join()
        http.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export_directory',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=json.dumps(run(args.export_directory),ensure_ascii=False,indent=2)
    print(result)
    if args.output:
        args.output.write_text(result+'\n',encoding='utf-8')

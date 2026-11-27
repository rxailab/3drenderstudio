"""Verify the cached photographic shadow on a real viewport and actual framebuffer."""
import argparse
import io
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import floor_shadow, open_project, project_server, sphere_geometry, white_backdrop


def run(software, preview):
    with project_server(backdrop='white') as (url, ids), sync_playwright() as pw:
        flags = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] if software else ['--enable-gpu']
        if not software and os.name == 'nt':
            flags.append('--use-angle=d3d11')
        with pw.chromium.launch(channel='chrome', headless=True, args=flags) as browser:
            page = browser.new_page(viewport={'width':1200, 'height':840}, device_scale_factor=1)
            errors = []
            page.on('console', lambda m: errors.append(m.text) if m.type=='error' and 'THREE' in m.text else None)
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.add_init_script("localStorage.setItem('wuying-viewport-trace','off')")
            page.goto(url)
            open_project(page, ids['base'])
            page.wait_for_function("realtime.instance?.frame.dataset.samples==='9'", timeout=90000)
            frame = page.locator('#realtimeViewport .realtime-canvas-frame')
            shot = Image.open(io.BytesIO(frame.screenshot())).convert('RGB')
            if preview:
                shot.save(preview)
            pixels = np.asarray(shot)
            assert not errors, errors
            ground = floor_shadow(page, pixels, sphere_geometry(page, 'Silver sphere'))
            ground['white_fraction'] = white_backdrop(pixels)
            atlas = page.evaluate("""() => {
              const v=realtime.instance,g=v.studioGround,out=new Uint8Array(g.result.width*g.result.height*4);
              v.renderer.readRenderTargetPixels(g.result,0,0,g.result.width,g.result.height,out);
              const hist=new Array(256).fill(0);
              for(let i=0;i<out.length;i+=4) hist[out[i]]++;
              let captures=0, bake=g.bake;
              g.bake=function(...args){captures++;return bake.apply(this,args);};
              window.getGroundCaptures=()=>captures;
              return {levels:hist.filter(n=>n).length,covered_fraction:1-hist[0]/(out.length/4)};
            }""")
            assert atlas['levels']>45 and .02<atlas['covered_fraction']<.85, atlas
            rect = frame.bounding_box()
            page.mouse.move(rect['x']+.5*rect['width'], rect['y']+.6*rect['height'])
            page.mouse.down()
            page.mouse.move(rect['x']+.62*rect['width'], rect['y']+.54*rect['height'], steps=12)
            page.mouse.up()
            page.wait_for_function("realtime.instance?.frame.dataset.samples==='9'", timeout=90000)
            assert page.evaluate('getGroundCaptures()')==0, 'A camera orbit must reuse the floor projection'
            return {**ground, **atlas, 'ground_rebakes_during_orbit':0}


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--software', action='store_true')
    parser.add_argument('--preview', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.software,args.preview), indent=2))

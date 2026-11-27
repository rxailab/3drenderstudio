"""Exercise the real viewport backdrop picker in Chrome on isolated projects.

From the project directory, run python tests/studio_backdrop_picker_smoke.py.
Uses SwiftShader for the interactive viewport. Add --lighting to compare
the actual interactive and path-traced lamp response on an NVIDIA GPU.
"""
import io
import json
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import project_server, open_project, sphere_geometry, disc, traced_frame
from studio_backdrops import catalog as backdrop_catalog, default_backdrop


ROOT = Path(__file__).resolve().parents[1]


def frame_pixels(page):
    frame = page.locator('#realtimeViewport .realtime-canvas-frame')
    return np.asarray(Image.open(io.BytesIO(frame.screenshot())).convert('RGB')).astype('int16')


def wait_selected(page, preset):
    page.wait_for_function(
        "id => realtime.instance.backdrop.id === id && studioBackdropUI.selection === id",
        arg=preset['id'], timeout=60000)
    assert page.locator('#viewportBackdropName').text_content() == preset['name']


def choose(page, preset):
    picker = page.locator('#viewportBackdrop')
    if not picker.evaluate('el => el.open'):
        page.locator('#viewportBackdropButton').click()
    page.select_option('#viewportBackdropGroup', label=preset['group'])
    page.fill('#viewportBackdropSearch', '')
    page.locator(f'#viewportBackdropGrid [data-backdrop="{preset["id"]}"]').click()
    wait_selected(page, preset)
    page.wait_for_function(
        "id => current.plan.viewport_backdrop===id && !document.querySelector('#viewportBackdrop').open",
        arg=preset['id'])
    assert not picker.evaluate('el => el.open')


def smoke():
    presets = backdrop_catalog()
    default, warm = presets[default_backdrop()], presets.get('cologne',presets['blue-keyboard'])
    outdoor = next((row for row in presets.values() if row['group'] == '户外'),presets['silver-beam'])
    errors, bad_responses, requests = [], [], Counter()

    with project_server() as (url, ids), sync_playwright() as pw, pw.chromium.launch(
        channel='chrome', headless=True, args=['--use-angle=swiftshader','--enable-unsafe-swiftshader']
    ) as browser:
        from studio_store import get,put
        white_project=get('project',ids['blue'])
        white_project['plan']['viewport_backdrop']='white'
        put('project',white_project)
        page = browser.new_page(viewport={'width':1536,'height':920}, device_scale_factor=1)
        page.set_default_timeout(60000)
        page.add_init_script("localStorage.setItem('wuying-viewport-trace','off')")
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.on('response', lambda res: bad_responses.append((res.status, res.url)) if res.status >= 400 else None)
        page.on('request', lambda req: requests.update([req.url]) if '/static/studio-backdrops/' in req.url else None)
        page.goto(url)
        open_project(page, ids['base'])
        wait_selected(page, default)
        assert default['id']=='silver-studio'
        assert get('project',ids['base'])['version']==1
        assert page.evaluate("""image => {
          const v=realtime.instance, source=v.plate?.image;
          return v.scene.background===v.plate && source?.naturalWidth>0 &&
              new URL(source.currentSrc).pathname===image;
        }""",default['image'])
        camera = page.evaluate("() => {window.beforeBackdrop=realtime.instance;return realtime.instance.snapshot()}")
        silver = frame_pixels(page)
        h,w=silver.shape[:2]
        gray=np.median(silver[round(h*.08):round(h*.12),round(w*.48):round(w*.52)],axis=(0,1))
        assert 100<gray.mean()<245 and gray.max()-gray.min()<25,gray
        picker, button = page.locator('#viewportBackdrop'), page.locator('#viewportBackdropButton')
        button.click()
        page.wait_for_function("n => document.querySelector('#viewportBackdropGrid').childElementCount === n", arg=len(presets))
        page.select_option('#viewportBackdropGroup', label=warm['group'])
        term = warm['examples'][0]
        page.fill('#viewportBackdropSearch', term)
        match = page.locator(f'#viewportBackdropGrid [data-backdrop="{warm["id"]}"]')
        assert match.is_visible() and term in match.get_attribute('title')
        page.keyboard.press('Escape')
        assert not picker.evaluate('el => el.open')
        choose(page, warm)
        assert page.evaluate("() => window.beforeBackdrop===realtime.instance && !dirty")
        selected_camera = page.evaluate("() => realtime.instance.snapshot()")
        assert selected_camera['projection'] == camera['projection']
        for key in ('azimuth','elevation','target_offset','ortho_scale'):
            np.testing.assert_allclose(selected_camera[key], camera[key], rtol=0, atol=1e-6)
        applied = frame_pixels(page)
        delta = abs(applied[:h//3] - silver[:h//3]).mean()
        assert delta > 8, delta
        button.click()
        page.wait_for_function("() => [...document.querySelectorAll('#viewportBackdropGrid img')].slice(0,6).every(i=>i.naturalWidth>0)")
        page.screenshot(path=str(ROOT / 'validation/viewport-backdrop-picker.png'))
        tile = page.locator('#localMaterialGrid .local-material-tile').first.bounding_box()
        target = page.evaluate("""() => {
          const view = realtime.instance, rect = view.renderer.domElement.getBoundingClientRect();
          for (const fx of [.35,.65,.45,.55]) for (const fy of [.55,.5,.6]) {
            const x = rect.left + fx*rect.width, y = rect.top + fy*rect.height;
            if (view.materialTargetAt(x,y)) return {x,y};
          } throw Error('No visible model for material drag');
        }""")
        page.mouse.move(tile['x']+tile['width']/2, tile['y']+tile['height']/2)
        page.mouse.down()
        assert not picker.evaluate('el => el.open')
        page.mouse.move(tile['x']+5, tile['y']+5, steps=5)
        page.mouse.move(target['x'], target['y'], steps=12)
        page.wait_for_function('() => !!realtime.instance.previewTarget')
        page.keyboard.press('Escape')
        page.mouse.up()
        page.wait_for_function('() => !realtime.instance.previewTarget')
        assert page.evaluate('current.version') == 2
        choose(page, outdoor)
        assert page.evaluate("() => window.beforeBackdrop===realtime.instance")
        button.click()
        page.fill('#viewportBackdropSearch', 'zzzzzzzzzzz')
        assert '没有匹配' in page.locator('#viewportBackdropStatus').inner_text()
        page.fill('#viewportBackdropSearch', '')
        page.keyboard.press('Escape')
        page.set_viewport_size({'width':390, 'height':844})
        button.scroll_into_view_if_needed()
        button.click()
        panel, viewer = picker.locator('.backdrop-panel').bounding_box(), page.locator('#viewer').bounding_box()
        assert panel['x'] >= max(0, viewer['x']) and panel['x']+panel['width'] <= min(391,viewer['x']+viewer['width']+1), (panel,viewer)
        assert panel['y']+panel['height'] <= viewer['y']+viewer['height'], (panel,viewer)
        summary, toggle = button.bounding_box(), page.locator('#viewportTraceToggle').bounding_box()
        assert summary['x']+summary['width'] <= toggle['x']+1, (summary,toggle)
        page.keyboard.press('Escape')
        page.set_viewport_size({'width':1536,'height':920})
        open_project(page, ids['blue'])
        wait_selected(page, presets['white'])
        assert page.evaluate("realtime.instance.scene.background.getHexString()")=='ffffff'
        assert get('project',ids['blue'])['version']==1
        open_project(page, ids['base'])
        wait_selected(page, outdoor)
        # Keep the render queued so this UI check inspects the real Flask job
        # snapshot without running a second 4K job alongside renderer tests.
        camera = page.evaluate('realtime.instance.snapshot()')
        with patch('engine.QUEUE.put'):
            page.locator('#renderButton').click()
            with page.expect_response(lambda r: r.request.method=='POST' and r.url.endswith('/jobs')) as submitted:
                page.locator('#renderConfirmDialog [data-choice="render"]').click()
            reply = submitted.value
            assert reply.status==202, reply.text()
            job = get('job',reply.json()['id'])
        assert job['plan']['viewport_backdrop']==outdoor['id']
        assert job['quality']=='standard' and 'hero' in job['shot_ids']
        shot = next(shot for shot in job['plan']['shots'] if shot['id']=='hero')
        assert shot['projection']==camera['projection']
        for field in ('azimuth','elevation','ortho_scale','target_offset','viewport_reference_diagonal'):
            np.testing.assert_allclose(shot[field],camera[field],atol=1e-7)
        page.wait_for_function("realtime.status==='ready' && realtime.record?.version===current.version")
        assert requests[url+'static/studio-backdrops/catalog.json'] == 1
        assert not errors, errors
        assert not bad_responses, bad_responses
        print(f'Passed {len(presets)} backgrounds, pixel difference {delta:.1f}, project persistence and 4K job camera.')


def lighting_smoke():
    # Keep the camera background and environment identical. Only the lamps
    # change, so product pixels measure their contribution to each renderer.
    with project_server() as (url, ids), sync_playwright() as pw, pw.chromium.launch(
        channel='chrome', headless=True, args=['--use-angle=d3d11','--enable-gpu']
    ) as browser:
        page=browser.new_page(viewport={'width':1200,'height':800})
        page.set_default_timeout(60000)
        page.add_init_script("localStorage.setItem('wuying-viewport-trace','off')")
        errors=[]
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.on('console', lambda msg: errors.append(msg.text) if 'Shader Error' in msg.text else None)
        page.goto(url)
        open_project(page, ids['base'])
        page.wait_for_function('studioBackdropCatalog?.presets.length')
        mirror=sphere_geometry(page,'Silver sphere')
        frames=[]
        assert not page.evaluate("performance.getEntriesByType('resource').some(e=>e.name.includes('RectAreaLightTexturesLib'))")
        for ident in ('silver-beam','charcoal-headphones','blue-night'):
            counts=page.evaluate("""async id=>{
              const v=realtime.instance;
              v.setTraceOptions({enabled:false});
              const sameSet=studioBackdropCatalog.presets.find(p=>p.id==='silver-studio');
              await v.setStudioBackdrop(id,{...sameSet,id,groundShadow:0});
              await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
              return [v.scene.children.filter(n=>n.isSpotLight).length,v.scene.children.filter(n=>n.isRectAreaLight).length];
            }""",ident)
            raster=frame_pixels(page)
            traced=traced_frame(page,2).astype('int16')
            state=page.evaluate('realtime.instance.pathTracing.state')
            assert 'NVIDIA' in state['adapterName'] and state['samples']>=2, state
            assert counts=={'silver-beam':[0,4],'charcoal-headphones':[1,0],'blue-night':[1,1]}[ident], counts
            print(ident,counts,state['adapterName'],state['samples'],flush=True)
            frames.append((raster,traced))
        assert page.evaluate("performance.getEntriesByType('resource').filter(e=>e.name.includes('RectAreaLightTexturesLib')).length===1")
        for a,b in zip(frames, frames[1:]):
            mask=disc(a[0],mirror)
            delta=[float(np.abs(x[mask]-y[mask]).mean()) for x,y in zip(a,b)]
            print('Mirror response, raster/traced:',delta,flush=True)
            assert all(d>1 for d in delta),delta
        assert not errors,errors


if __name__ == '__main__':
    if '--lighting' in sys.argv:
        lighting_smoke()
    else:
        smoke()

"""Exercise the changed-camera prompt with a real orbit and two isolated Cycles renders."""
import argparse
import io
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import open_project, project_server
from viewport_final_render_smoke import read_url


def completed_job(url, job_id):
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        job = json.loads(read_url(url, '/api/jobs/' + job_id))
        if job['status'] in ('completed', 'failed', 'interrupted', 'cancelled'):
            assert job['status'] == 'completed', (job['status'], job.get('error'))
            return job
        time.sleep(.5)
    raise TimeoutError('Cycles preview did not finish')


def check_pose(actual, expected, angle_tolerance=1e-4):
    assert actual['projection'] == expected['projection']
    for key in ('azimuth', 'elevation'):
        np.testing.assert_allclose(actual[key], expected[key], atol=angle_tolerance)
    np.testing.assert_allclose(actual['target_offset'], expected['target_offset'], atol=1e-4)
    np.testing.assert_allclose(actual['ortho_scale'] / actual['viewport_reference_diagonal'],
                               expected['ortho_scale'] / expected['viewport_reference_diagonal'],
                               rtol=1e-4)


def run(output):
    with project_server() as (url, ids):
        import engine
        from studio_store import get, put

        ident = ids['base']
        project = get('project', ident)
        project['plan'].update(visual_review=False, auto_polish=False, viewport_backdrop='white')
        put('project', project)
        engine.start()
        baseline = engine.submit(ident, 'preview', 'preview', ['hero'], expected_version=project['version'])
        completed_job(url, baseline['id'])
        old = next(o for o in get('project', ident)['outputs'] if o['job_id'] == baseline['id'])
        print('Baseline preview complete', flush=True)
        with sync_playwright() as pw, pw.chromium.launch(
                channel='chrome', headless=True, args=['--use-angle=d3d11', '--enable-gpu']) as browser:
            page = browser.new_page(viewport={'width':1350, 'height':800}, device_scale_factor=1)
            page.set_default_timeout(60000)
            page.add_init_script("localStorage.setItem('wuying-viewport-trace','off')")
            requests, errors = [], []

            def observe(request):
                if request.method == 'POST' and request.url.endswith(('/jobs', '/plan')):
                    requests.append((request.url.rsplit('/', 1)[1], request.post_data_json))

            page.on('request', observe)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            open_project(page, ident)
            live = page.locator('#liveViewButton')
            saved = page.locator('#savedViewButton')
            dialog = page.locator('#refreshViewDialog')
            image = page.locator('#renderImage')

            def show_live():
                live.click()
                page.wait_for_function("realtimeActive() && realtime.status==='ready' && realtime.instance")

            def choice(value):
                dialog.locator(f'[data-choice="{value}"]').first.click()
                dialog.wait_for(state='hidden')

            # The initial camera matches the existing image.
            saved.click()
            image.wait_for(state='visible')
            assert not dialog.is_visible() and image.get_attribute('src') == old['png_url']
            show_live()
            initial = page.evaluate('realtime.instance.snapshot()')
            frame = page.locator('#realtimeViewport .realtime-canvas-frame').bounding_box()
            x = frame['x'] + frame['width'] * .55
            y = frame['y'] + frame['height'] * .60
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 90, y + 8, steps=12)
            page.mouse.up()
            page.wait_for_function('realtime.changed && !realtime.instance.frameRequest')
            desired = page.evaluate('realtime.instance.snapshot()')
            assert abs(desired['azimuth'] - initial['azimuth']) > 5
            saved.click()
            choice('cancel')
            assert page.evaluate('realtimeActive()')
            check_pose(page.evaluate('realtime.instance.snapshot()'), desired)
            saved.click()
            choice('existing')
            image.wait_for(state='visible')
            assert image.get_attribute('src') == old['png_url'] and not requests, requests
            show_live()
            # Re-showing the canvas draws a frame and resumes the final
            # sub-pixel residual from OrbitControls damping.
            page.wait_for_function('!realtime.instance.frameRequest')
            check_pose(page.evaluate('realtime.instance.snapshot()'), desired, angle_tolerance=.05)
            saved.click()
            dialog.wait_for(state='visible')
            desired = page.evaluate('realtime.instance.snapshot()')
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/jobs')) as queued:
                choice('refresh')
            response = queued.value
            assert response.status == 202, response.text()
            job = response.json()
            assert job['kind'] == 'preview' and job['shot_ids'] == ['hero'], job
            check_pose(next(s for s in job['plan']['shots'] if s['id'] == 'hero'), desired)
            assert [endpoint for endpoint, _ in requests] == ['plan', 'jobs'], requests
            posted_shot = next(s for s in requests[0][1]['shots'] if s['id'] == 'hero')
            check_pose(posted_shot, desired)
            print('Refresh submitted; the POST contains the live camera', flush=True)
            completed_job(url, job['id'])
            new = next(o for o in get('project', ident)['outputs'] if o['job_id'] == job['id'])
            assert new['png_url'] != old['png_url']
            page.wait_for_function("""src => {
              const img=document.querySelector('#renderImage');
              return !img.hidden && img.getAttribute('src')===src && img.complete && img.naturalWidth>0;
            }""", arg=new['png_url'])
            show_live()
            saved.click()
            image.wait_for(state='visible')
            assert not dialog.is_visible() and len(requests) == 2 and not errors, (requests, errors)
            read_image = lambda src: np.array(Image.open(io.BytesIO(read_url(url, src))).convert('RGB'), dtype=np.int16)
            pixels = np.abs(read_image(old['png_url']) - read_image(new['png_url'])).max(axis=2)
            changed = float((pixels > 16).mean())
            assert changed > .025, changed
            result = json.loads(read_url(url, new['result_url']))
            assert result['render_settings']['engine'] == 'CYCLES'
            report = dict(initial_camera=initial, refreshed_camera=posted_shot, requests=[k for k, _ in requests],
                          preview_size=[new['width'], new['height']], old_image_url=old['png_url'],
                          new_image_url=new['png_url'], changed_pixel_fraction=round(changed, 4),
                          render_device=result.get('device'), renderer=result['render_settings']['engine'])
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            if output:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    run(parser.parse_args().output)

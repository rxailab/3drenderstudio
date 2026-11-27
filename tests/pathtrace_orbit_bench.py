"""Measure main-view orbit frames while interrupting an active GPU trace.

Run python tests/pathtrace_orbit_bench.py on the machine under test.
"""
import argparse
import json
import math
import os
from pathlib import Path
from time import perf_counter

from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import open_project, project_server


def measure(page):
    canvas = page.locator('#realtimeViewport canvas').first
    surface = canvas.bounding_box()
    x = surface['x'] + surface['width'] * .5
    y = surface['y'] + surface['height'] * .6
    page.mouse.move(x, y)
    page.evaluate("""() => {
      const v = realtime.instance, renderer = v.renderer, camera = v.camera;
      const canvas = renderer.domElement;
      const probe = window.orbitProbe = {frames:[], moves:0, active:false};
      const start = () => { probe.active = true; };
      const move = event => {
        if (probe.active && event.buttons) {
          if (!probe.moves) probe.firstInput = performance.now();
          probe.moves++;
        }
      };
      const end = () => { probe.active = false; probe.released = performance.now(); };
      canvas.addEventListener('pointerdown', start, true);
      canvas.addEventListener('pointermove', move, true);
      canvas.addEventListener('pointerup', end, true);
      const draw = renderer.render;
      let last = camera.position.clone();
      renderer.render = function (scene, view, ...args) {
        const result = draw.call(this, scene, view, ...args);
        if (probe.active && probe.moves && scene === v.scene && view === camera &&
            !last.equals(camera.position)) {
          probe.frames.push(performance.now());
          last.copy(camera.position);
        }
        return result;
      };
      probe.finish = () => {
        renderer.render = draw;
        canvas.removeEventListener('pointerdown', start, true);
        canvas.removeEventListener('pointermove', move, true);
        canvas.removeEventListener('pointerup', end, true);
        return {frames:probe.frames, moves:probe.moves, firstInput:probe.firstInput};
      };
    }""")
    page.mouse.down()
    began = perf_counter()
    duration = 3.0
    while True:
        fraction = min(1, (perf_counter() - began) / duration)
        page.mouse.move(x + surface['width'] * .22 * fraction,
                        y + surface['height'] * .075 * math.sin(fraction * math.pi))
        if fraction >= 1:
            break
        page.wait_for_timeout(8)
    during = page.evaluate('() => ({...realtime.instance.frame.dataset})')
    page.mouse.up()
    data = page.evaluate('orbitProbe.finish()')
    assert len(data['frames']) >= 5, 'The scene must actually draw changing camera poses'
    assert during.get('traceSamples') == '0', during
    frames = data['frames']
    intervals = sorted(b - a for a, b in zip(frames, frames[1:]))
    def percentile(p):
        return round(intervals[math.ceil(p * len(intervals)) - 1], 2)
    return {'pointer_moves':data['moves'], 'changed_camera_frames':len(frames),
            'first_changed_frame_ms':round(frames[0] - data['firstInput'], 2),
            'frame_interval_p50_ms':percentile(.50), 'frame_interval_p95_ms':percentile(.95),
            'longest_frame_interval_ms':round(intervals[-1], 2),
            'canvas_css_px':[round(surface['width']), round(surface['height'])]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with project_server() as (url, projects), sync_playwright() as playwright:
        flags = ['--enable-gpu'] + (['--use-angle=d3d11'] if os.name == 'nt' else [])
        with playwright.chromium.launch(channel='chrome', headless=True, args=flags) as browser:
            page = browser.new_page(viewport={'width':1480, 'height':930}, device_scale_factor=1)
            page.goto(url)
            began = perf_counter()
            open_project(page, projects['base'])
            ready_ms = round((perf_counter()-began)*1000)
            first = "() => realtime.instance.frame.dataset.traceMode==='pathtracing' && Number(realtime.instance.frame.dataset.traceSamples)>=1"
            page.wait_for_function(first, timeout=180000)
            first_ms = round((perf_counter()-began)*1000)
            active = """() => realtime.instance.frame.dataset.traceMode==='pathtracing'
              && Number(realtime.instance.frame.dataset.traceSamples)>=2"""
            page.wait_for_function(active, timeout=180000)
            report = measure(page)
            report.update(cold_open_to_ready_ms=ready_ms,cold_open_to_first_trace_ms=first_ms)
            page.wait_for_function(first, timeout=60000)
            report['release_to_first_trace_ms'] = round(page.evaluate('performance.now()-orbitProbe.released'))
            page.wait_for_function(active, timeout=60000)
            report['resumed_samples'] = int(page.locator('.realtime-canvas-frame').get_attribute('data-trace-samples'))
            report['webgl_adapter'] = page.evaluate("""() => {
              const gl = realtime.instance.renderer.getContext(), ext = gl.getExtension('WEBGL_debug_renderer_info');
              return gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
            }""")
            value = json.dumps(report, ensure_ascii=False, indent=2)
            print(value)
            if args.output:
                args.output.write_text(value + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

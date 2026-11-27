"""Check the real render-image / live-3D buttons against NVIDIA worker progress."""
import argparse
import hashlib
import json
import subprocess

from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import ROOT, open_project, project_server
from viewport_refresh_image_smoke import completed_job


MONITOR = """() => {
  const pt=realtime.instance.pathTracing;
  const show=pt.showFrame;
  window.seenSamples=[];
  pt.showFrame=function(bitmap,n){
    seenSamples.push(n);
    return show.call(this,bitmap,n);
  };
  const send=Worker.prototype.postMessage;
  window.sentRuns=0;
  Worker.prototype.postMessage=function(msg,...rest){
    if(msg?.kind==='run')sentRuns++;
    return send.call(this,msg,...rest);
  };
}"""


def check(reference=None):
    with project_server(backdrop='white') as (url, ids):
        import engine
        from studio_store import get, put
        if reference:
            from app import app
            from flask import Response, request
            modules={}
            for name in ('app','viewport','viewport-path-tracing','viewport-trace-worker','viewport-refinement','viewport-trace-schedule'):
                filename=f'static/{name}.js'
                modules['/'+filename]=subprocess.check_output(['git','show',f'{reference}:{filename}'],cwd=ROOT)
            @app.before_request
            def serve_reference():
                if request.path in modules:
                    return Response(modules[request.path],mimetype='text/javascript')
        ident=ids['base']
        project=get('project',ident)
        project['plan'].update(visual_review=False,auto_polish=False)
        put('project',project)
        engine.start()
        job=engine.submit(ident,'preview','preview',['hero'],expected_version=project['version'])
        completed_job(url,job['id'])
        with sync_playwright() as pw, pw.chromium.launch(channel='chrome',headless=True,
                args=['--enable-gpu','--use-angle=d3d11']) as browser:
            page=browser.new_page(viewport={'width':1280,'height':720},device_scale_factor=1)
            page.set_default_timeout(180000)
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url)
            open_project(page,ident)
            canvas=page.locator('#realtimeViewport canvas').first
            live=page.locator('#liveViewButton')
            saved=page.locator('#savedViewButton')
            page.wait_for_function("realtime.instance.pathTracing.state.samples===128 || realtime.instance.pathTracing.state.mode==='fallback'")
            state=page.evaluate('realtime.instance.pathTracing.state')
            assert state['samples']==128 and 'NVIDIA' in state['adapterName'],state
            page.evaluate(MONITOR)
            image_hash=lambda:hashlib.sha256(canvas.screenshot()).hexdigest()
            before=image_hash()
            saved.click()
            page.locator('#renderImage').wait_for(state='visible')
            live.click()
            canvas.wait_for(state='visible')
            # Allow the normal settle timer to run, revealing an unintended new worker run.
            page.wait_for_timeout(900)
            after=image_hash()
            completed=page.evaluate('({samples:realtime.instance.pathTracing.state.samples,runs:sentRuns})')
            completed['pixels_equal']=before==after
            print('Restoring completed frame:',completed,flush=True)
            page.wait_for_function('realtime.instance.pathTracing.state.samples===128')
            page.evaluate('seenSamples.length=0')
            canvas.press('ArrowRight')
            page.wait_for_function('realtime.changed && seenSamples.some(n=>n>=8 && n<128)')
            # Switch synchronously in the page before another tile message can be handled.
            partial=page.evaluate("""() => {
                const n=realtime.instance.pathTracing.state.samples;
                document.querySelector('#savedViewButton').click();
                document.querySelector('#refreshViewDialog [data-choice=existing]').click();
                return n;
            }""")
            assert 8<=partial<128,partial
            page.locator('#renderImage').wait_for(state='visible')
            page.wait_for_timeout(400)
            changed=page.evaluate('seenSamples.slice()')
            assert changed[0]<partial and changed[0]<=8,changed
            page.evaluate('seenSamples.length=0')
            live.click()
            page.wait_for_function('seenSamples.length>0 && realtime.instance.pathTracing.state.samples===128')
            resumed=page.evaluate('seenSamples.slice()')
            report=dict(adapter=state['adapterName'],completed=completed,camera_change_first=changed[0],
                        paused_at=partial,first_after_resume=resumed[0],finished=resumed[-1])
            print(json.dumps(report,ensure_ascii=False),flush=True)
            assert not errors,errors
            if not reference:
                assert completed==dict(samples=128,runs=0,pixels_equal=True),completed
                assert resumed[0]>=partial and resumed[-1]==128,resumed


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare-revision',help='Report baseline behavior using these Git revision assets.')
    check(parser.parse_args().compare_revision)

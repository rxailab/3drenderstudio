"""Measure homepage GPU precompilation, opening models and orbiting in real Chrome.

Each scenario starts Chrome with a fresh browser profile. Fixture exports are
prepared before timing to keep Blender export latency out of the GPU comparison.
Use --old-modules with a snapshot of app.js, index.html and viewport modules.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import inspect_metal, open_project, project_server, sphere_geometry
from pathtrace_orbit_bench import measure
from studio_backdrop_picker_smoke import choose
from viewport_optics_bench import ROOT, capture
from studio_backdrops import catalog as backdrop_catalog


PROBE = """(() => {
  const probe=window.warmProbe={events:[],opened:[],workers:0};
  const log=(row)=>probe.events.push({...row,ms:performance.now()});
  const BuiltinWorker=window.Worker;
  window.Worker=class extends BuiltinWorker {
    constructor(...args) {
      super(...args);
      if(!String(args[0]).includes('viewport-trace-worker'))return;
      const id=++probe.workers;
      log({id,kind:'created'});
      this.addEventListener('message',({data})=>{
        const {kind,epoch,samples,stage,cache,profile,adapterName}=data;
        if(kind!=='frame')log({id,kind,epoch,stage,cache,profile,adapterName});
      });
      const send=this.postMessage;
      this.postMessage=function(message,...rest){
        if(!['presented','pause'].includes(message.kind))
          log({id,kind:'send-'+message.kind,epoch:message.epoch});
        return send.call(this,message,...rest);
      };
    }
  };
  window.addEventListener('click',event=>{
    const card=event.target.closest('[data-open-project]');
    if(card)probe.opened.push({project:card.dataset.openProject,click:performance.now(),frames:[]});
  },true);
  window.addEventListener('wuying-viewport-ready',()=>{
    const create=window.WuyingViewport.create;
    window.WuyingViewport.create=(...args)=>{
      const view=create(...args), sample=probe.opened.at(-1);
      if(!sample)return view;
      const load=view.load;
      view.load=async function(...params){
        const result=await load.apply(this,params);
        sample.loaded=performance.now(); return result;
      };
      const observer=new MutationObserver(()=>{
        if(view.frame.dataset.previewOptics!=='ready')return;
        observer.disconnect();
        const gl=view.renderer.getContext();
        // Read one real pixel after the first interactive draw has completed.
        gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array(4));
        sample.raster=performance.now();
        sample.rasterSamples=Number(view.frame.dataset.traceSamples);
        sample.reflectionProbes=view.fastOptics.probes.length;
      });
      observer.observe(view.frame,{attributes:true,attributeFilter:['data-preview-optics']});
      const path=view.pathTracing, show=path.showFrame;
      path.showFrame=function(bitmap,samples){
        const size=[bitmap.width,bitmap.height];
        show.call(this,bitmap,samples);
        sample.frames.push({ms:performance.now(),epoch:this.epoch,samples,size});
      };
      return view;
    };
  });
})();"""


def prepare_exports(projects):
    from studio_store import get
    from viewport_service import read, submit
    for ident in projects:
        p=get('project',ident)
        row=submit(ident,{'shot_id':p['plan']['shots'][0]['id'],'expected_version':p['version']})
        deadline=time.monotonic()+90
        while row['status'] in ('queued','running') and time.monotonic()<deadline:
            time.sleep(.15)
            row=read(ident,row['id'])
        assert row['status']=='ready',row


def snapshot_routes(directory):
    from app import app
    from flask import request, send_file
    files={('/static/'+p.relative_to(directory).as_posix()):p.resolve() for p in directory.rglob('*') if p.is_file()}
    if '/static/index.html' in files:
        files['/']=files['/static/index.html']
    @app.before_request
    def serve_previous():
        path=files.get(request.path)
        if path:
            mime={'.html':'text/html','.css':'text/css','.js':'text/javascript','.mjs':'text/javascript'}
            return send_file(path,mimetype=mime.get(path.suffix))


def wait_frames(page, n):
    page.wait_for_function("""n=>realtime.instance?.pathTracing?.state.mode==='fallback' ||
        (warmProbe.opened.at(-1).frames.at(-1)?.samples>=n && realtime.instance?.pathTracing?.state.samples>=n)""",
        arg=n,timeout=180000)
    state=page.evaluate('realtime.instance.pathTracing.state')
    assert state['mode']=='pathtracing' and state['samples']>=n and 'NVIDIA' in state['adapterName'],state


def opening(page,ident):
    open_project(page,ident)
    wait_frames(page,128)
    sample=page.evaluate('warmProbe.opened.at(-1)')
    first=sample['frames'][0]
    assert first['samples']==1 and all(n>4 for n in first['size']),first
    assert sample['rasterSamples']==0 and sample['reflectionProbes']>0,sample
    assert sample['click']<=sample['raster']<=first['ms'],sample
    events=page.evaluate('warmProbe.events')
    run=next(e for e in events if e['kind']=='send-run' and e['ms']>=sample['click'] and e.get('epoch')==first['epoch'])
    kernel=next((e for e in events if e['id']==run['id'] and e['kind']=='kernel' and e.get('epoch')==first['epoch']
                 and run['ms']<=e['ms']<=first['ms']),{})
    result=dict(click_ms=round(sample['click']),click_to_model_ready_ms=round(sample['loaded']-sample['click']),
                click_to_optics_ms=round(sample['raster']-sample['click']),click_to_trace_ms=round(first['ms']-sample['click']),
                optics_to_trace_ms=round(first['ms']-sample['raster']),worker_run_to_first_ms=round(first['ms']-run['ms']),
                click_to_128_ms=round(sample['frames'][-1]['ms']-sample['click']),worker_id=run['id'],
                cache=kernel.get('cache'),profile=kernel.get('profile'),trace_size=first['size'],
                adapter=page.evaluate('realtime.instance.pathTracing.state.adapterName'))
    print('Opened',result,flush=True)
    return result


def scenario(pw,url,ids,name,home_wait):
    with pw.chromium.launch(channel='chrome',headless=True,args=['--use-angle=d3d11','--enable-gpu']) as browser:
        page=browser.new_page(viewport={'width':1470,'height':860},device_scale_factor=1)
        page.set_default_timeout(90000)
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('console',lambda m:errors.append(m.text) if 'Shader Error' in m.text else None)
        page.add_init_script(PROBE)
        page.goto(url,wait_until='domcontentloaded')
        page.locator(f'#homeProjectGrid [data-open-project="{ids["base"]}"]').wait_for()
        home=page.evaluate('performance.now()')
        if name=='home':
            page.wait_for_timeout(home_wait)
        if name=='early-drag':
            open_project(page,ids['base'])
            page.wait_for_function("warmProbe.opened[0]?.raster")
            had_frame=page.evaluate("!!warmProbe.opened[0].frames.length")
            drag=measure(page)
            page.wait_for_function("Number(realtime.instance.frame.dataset.traceSamples)>=1",timeout=90000)
            result={'home_ready_ms':round(home),'trace_presented_before_drag':had_frame,'drag':drag}
            result['release_to_trace_ms']=round(page.evaluate('performance.now()-orbitProbe.released'))
        else:
            first=opening(page,ids['base'])
            result={'home_ready_ms':round(home),'first':first}
            if name=='remembered':
                catalog=backdrop_catalog()
                photo=catalog.get('cologne',catalog['silver-beam'])
                choose(page,photo)
                page.wait_for_function("""() => realtime.instance.pathTracing.state.samples===128 &&
                    warmProbe.events.filter(e=>e.kind==='kernel').at(-1)?.profile?.background""",timeout=90000)
                profile=page.evaluate("warmProbe.events.filter(e=>e.kind==='kernel').at(-1).profile")
                page.goto(url)
                page.wait_for_function("""p=>warmProbe.events.some(e=>e.kind==='warmed' &&
                    e.profile.features===p.features && e.profile.background && e.profile.camera===p.camera)""",arg=profile)
                result['reopen']=opening(page,ids['base'])
                page.wait_for_function("id=>realtime.instance.backdrop.id===id && realtime.instance.pathTracing.state.samples===128",arg=photo['id'])
                linked=page.evaluate("warmProbe.events.filter(e=>e.kind==='kernel' && e.profile?.background).at(-1)")
                assert linked['profile']==profile and linked['cache']=='prewarmed',linked
                result['restored_profile']=profile
            elif name=='home':
                box=page.locator('#realtimeViewport .realtime-canvas-frame').bounding_box()
                size=tuple(round(box[k]) for k in ('width','height'))
                mirror=sphere_geometry(page,'Silver sphere')
                before=np.array(capture(page,size))
                switched=opening(page,ids['blue'])
                result['switch']=switched
                result['verified_mirror']=inspect_metal(before,np.array(capture(page,size)),mirror)
                page.evaluate('realtime.instance.requestDraw()')
                wait_frames(page,1)
                result['drag']=measure(page)
                page.wait_for_function("Number(realtime.instance.frame.dataset.traceSamples)>=1",timeout=90000)
                result['release_to_trace_ms']=round(page.evaluate('performance.now()-orbitProbe.released'))
        result['events']=page.evaluate('warmProbe.events')
        assert not errors,errors
        return result


def main(args):
    result={}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with project_server() as (url,ids),sync_playwright() as pw:
        if args.old_modules:
            snapshot_routes(args.old_modules)
        prepare_exports([ids['base'],ids['blue']])
        for name in args.scenarios:
            print('Starting fresh Chrome:',name,flush=True)
            result[name]=scenario(pw,url,ids,name,args.home_wait_ms)
            args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
            print('Recorded',name,'in',args.output,flush=True)
            if name=='home' and not args.old_modules:
                run=result[name]
                assert any(e['kind']=='warmed' and e['ms']<run['first']['click_ms'] for e in run['events']),run
                assert run['first']['cache'] in ('prewarmed','reused'),run['first']
                assert run['first']['worker_id']==run['switch']['worker_id'],run['switch']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--old-modules',type=Path)
    parser.add_argument('--home-wait-ms',type=int,default=12000)
    parser.add_argument('--scenarios',nargs='+',choices=['immediate','home','early-drag','remembered'],
                        default=['immediate','home','early-drag'])
    main(parser.parse_args())

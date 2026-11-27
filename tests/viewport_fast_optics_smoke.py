"""Check the first submitted viewport frame, before idle AA and full path tracing.

Run python tests/viewport_fast_optics_smoke.py --output validation on the GPU host.
"""
import argparse
import base64
import io
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import (
    floor_shadow, inspect_glass, inspect_metal, open_project, project_server,
    sphere_geometry, white_backdrop, write_comparison,
)


PROBE = """(() => {
  localStorage.removeItem('wuying-viewport-trace');
  window.addEventListener('click',event=>{
    if(event.target.closest('[data-open-project]')) window.firstOptics={click:performance.now()};
  },true);
  window.addEventListener('wuying-viewport-ready',()=>{
    const create=window.WuyingViewport.create;
    window.WuyingViewport.create=(...args)=>{
      const view=create(...args), load=view.load;
      view.load=async function(...params){
        const result=await load.apply(view,params);
        window.firstOptics.ready=performance.now();
        return result;
      };
      const observer=new MutationObserver(()=>{
        if(view.frame.dataset.previewOptics!=='ready')return;
        observer.disconnect();
        const canvas=view.renderer.domElement, copy=document.createElement('canvas'), stats=window.firstOptics;
        copy.width=canvas.width;copy.height=canvas.height;
        const ctx=copy.getContext('2d',{willReadFrequently:true});
        ctx.drawImage(canvas,0,0);
        ctx.getImageData(0,0,1,1); // Wait for the actual rendered pixels.
        stats.visible=performance.now();
        stats.canvas=copy;
        stats.aa=Number(view.frame.dataset.samples);
        stats.rays=Number(view.frame.dataset.traceSamples);
        stats.probes=view.fastOptics.probes.length;
        const gl=view.renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');
        stats.adapter=gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
      });
      observer.observe(view.frame,{attributes:true,attributeFilter:['data-preview-optics']});
      return view;
    };
  });
})();"""


def first_frame(page, ident):
    open_project(page, ident)
    page.wait_for_function('window.firstOptics?.visible && window.firstOptics.ready')
    observed = page.evaluate("""() => {
      const {click,ready,visible,aa,rays,adapter,canvas,probes}=window.firstOptics;
      return {project_to_ready_ms:Math.round(ready-click),project_to_first_optics_ms:Math.round(visible-click),
        ready_to_first_optics_ms:Math.round(visible-ready),first_frame_aa_samples:aa,first_frame_path_samples:rays,
        reflection_probes:probes,adapter,pixels:canvas.toDataURL('image/png').split(',')[1]};
    }""")
    image = Image.open(io.BytesIO(base64.b64decode(observed.pop('pixels')))).convert('RGB')
    assert observed['first_frame_path_samples']==observed['first_frame_aa_samples']==0,observed
    assert observed['reflection_probes']>=1,observed
    return np.asarray(image).copy(),observed


def check_handoff(page):
    # Read the actual framebuffer three ways for the same sample: the visible
    # result, the fully traced product, and the original antialiased fast view.
    page.evaluate("""() => {
      const v=realtime.instance, r=v.refinement, show=r.presentTrace;
      const gl=v.renderer.getContext(), w=gl.drawingBufferWidth, h=gl.drawingBufferHeight;
      const read=()=>{const p=new Uint8Array(w*h*4);gl.readPixels(0,0,w,h,gl.RGBA,gl.UNSIGNED_BYTE,p);return p;};
      window.handoffFrames={};
      r.presentTrace=function(texture,samples,limit){
        show.call(this,texture,samples,limit);
        if(limit!==8 || samples in handoffFrames)return;
        const observed=read(),weight=this.traceDisplay.uniforms.traceWeight.value;
        show.call(this,texture,samples,samples);
        const full=read();
        this.present();
        const raster=read();
        show.call(this,texture,samples,limit);
        const ratios=[];
        for(let i=0;i<observed.length;i+=4*9){
          const step=full[i]-raster[i];
          if(Math.abs(step)>20)ratios.push((observed[i]-raster[i])/step);
        }
        ratios.sort((a,b)=>a-b);
        handoffFrames[samples]={expected:weight,observed:ratios[Math.floor(ratios.length/2)],pixels:ratios.length};
      };
      v.setTraceOptions({enabled:true,limit:8});
    }""")
    page.wait_for_function('8 in handoffFrames',timeout=100000)
    frames=page.evaluate('handoffFrames')
    samples=sorted(int(key) for key in frames)
    assert samples[0]==1 and samples[-1]==8,frames
    for row in frames.values():
        assert row['pixels']>50 and abs(row['observed']-row['expected'])<.04,row
    weights=[frames[str(sample)]['observed'] for sample in samples]
    assert weights[0]>0 and weights[-1]==1 and all(a<b for a,b in zip(weights,weights[1:])),frames
    return frames


def main(output,software=False):
    output.mkdir(parents=True, exist_ok=True)
    flags=(['--use-angle=swiftshader','--enable-unsafe-swiftshader'] if software else
           ['--enable-gpu']+(['--use-angle=d3d11'] if os.name=='nt' else []))
    with project_server(backdrop='white') as (url, ids), sync_playwright() as pw:
        with pw.chromium.launch(channel='chrome',headless=True,args=flags) as browser:
            page=browser.new_page(viewport={'width':1320,'height':800},device_scale_factor=1)
            errors=[]
            page.on('pageerror', lambda e:errors.append(str(e)))
            page.on('console', lambda m:errors.append(m.text) if 'Shader Error' in m.text or 'Computed radius is NaN' in m.text else None)
            page.add_init_script(PROBE)
            page.goto(url)
            base,timing=first_frame(page,ids['base'])
            print(timing,flush=True)
            software_driver=any(s in timing['adapter'].lower() for s in ('swiftshader','llvmpipe','basic render','software'))
            assert software_driver==software,timing
            mirror=sphere_geometry(page,'Silver sphere')
            glass=sphere_geometry(page,'Glass sphere')
            backdrop=white_backdrop(base)
            shadow=floor_shadow(page,base,mirror)
            # Put the captured first-frame pixels back over the same live canvas
            # for the full-window screenshot, preserving the entire application UI.
            page.evaluate("""() => {
              const img=window.firstOptics.canvas;
              Object.assign(img.style,{position:'absolute',inset:'0',width:'100%',height:'100%',pointerEvents:'none'});
              realtime.instance.frame.append(img);
            }""")
            page.screenshot(path=str(output/'viewport-fast-preview-main.png'))
            blue,_=first_frame(page,ids['blue'])
            air,_=first_frame(page,ids['air'])
            red_to_blue=inspect_metal(base,blue,mirror)
            refraction=inspect_glass(base,air,glass)
            handoff=check_handoff(page) if not software else {}
            assert not errors,errors
            write_comparison(output/'viewport-fast-optics-comparison.png',base,blue,air,mirror,glass)
            report={**timing,'mirror':red_to_blue,'glass':refraction,'white_backdrop_fraction':backdrop,'floor_shadow':shadow,
                    'measured_handoff_weights':handoff}
            (output/'viewport-fast-optics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('validation'))
    parser.add_argument('--software',action='store_true')
    args=parser.parse_args()
    main(args.output,args.software)

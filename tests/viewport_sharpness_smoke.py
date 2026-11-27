"""Measure the real 128-sample viewport at different device pixel ratios.

The removable chart uses geometry in the isolated optical fixture. Subpixel
line pairs and a slanted edge have known positions independently of texture
mipmapping. The diffusely lit patch measures noise in the same frame.
"""
import argparse
import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from pathtrace_optics_smoke import open_project, project_server
from pathtrace_orbit_bench import measure
from viewport_prewarm_bench import prepare_exports, snapshot_routes


INSTALL_CHART = """async () => {
  const T=await import('/static/vendor/three/build/three.module.js');
  const v=realtime.instance, c=v.camera, r=v.frame.getBoundingClientRect();
  if(!c.isOrthographicCamera) throw Error('The chart requires an orthographic camera');
  const chart=new T.Group();
  chart.position.copy(v.controls.target).addScaledVector(c.getWorldDirection(new T.Vector3()),-v.bounds.diagonal*.75);
  chart.quaternion.copy(c.quaternion);
  const scale=(c.right-c.left)/(c.zoom*r.width);
  const left=Math.round(r.width*.10),right=Math.round(r.width*.90),top=Math.round(r.height*.048);
  const slope=1/8, rowHeight=Math.round(r.height*.044);
  const low=new T.MeshBasicMaterial({color:new T.Color().setRGB(.18,.18,.18)});
  const high=new T.MeshBasicMaterial({color:new T.Color().setRGB(.30,.30,.30)});
  const matte=new T.MeshStandardMaterial({color:new T.Color().setRGB(.28,.28,.28),roughness:.85});
  const batches=new Map([[low,[]],[high,[]],[matte,[]]]);
  const quad=(m,xa,xb,xc,xd,yt,yb)=>{
    const positions=batches.get(m);
    for(const [x,y] of [[xa,yt],[xc,yb],[xb,yt],[xb,yt],[xc,yb],[xd,yb]]){
      positions.push((x-r.width/2)*scale,(r.height/2-y)*scale,0);
    }
  };
  const widths=[1,1.5,2.5,4];
  for(const [i,width] of widths.entries()){
    const y=top+(rowHeight+2)*i;
    const shift=slope*rowHeight;
    for(let n=0;left+n*width<right;n++){
      const x=left+n*width;
      quad(n%2?high:low,x,x+width,x+shift,x+shift+width,y,y+rowHeight);
    }
  }
  const edgeTop=top+(rowHeight+2)*widths.length, edgeBottom=edgeTop+rowHeight*1.5;
  const center=Math.floor((left+right)/2), bottomCenter=center+slope*(edgeBottom-edgeTop);
  quad(low,left,center,left,bottomCenter,edgeTop,edgeBottom);
  quad(high,center,right,bottomCenter,right,edgeTop,edgeBottom);
  const patchTop=edgeBottom+2, patchBottom=patchTop+rowHeight;
  quad(matte,left,right,left,right,patchTop,patchBottom);
  for(const [mat,vertices] of batches){
    const geometry=new T.BufferGeometry();
    geometry.setAttribute('position',new T.Float32BufferAttribute(vertices,3));
    geometry.computeVertexNormals();
    chart.add(new T.Mesh(geometry,mat));
  }
  // A small printed detail occupies only part of a large texture atlas. Its
  // screen-space size and its source texel width are independently known.
  const tex=document.createElement('canvas'); tex.width=tex.height=2048;
  const ctx=tex.getContext('2d'), textureSpan=384;
  ctx.fillStyle='rgb(118,118,118)';ctx.fillRect(0,0,2048,2048);
  ctx.fillStyle='rgb(149,149,149)';
  const textureTop=patchBottom+2;
  const atlas=new T.CanvasTexture(tex);
  atlas.colorSpace=T.SRGBColorSpace;
  const printMaterial=new T.MeshBasicMaterial({map:atlas});
  for(const stride of [1,2]){
    const base=(stride-1)*1024;
    for(let x=32+stride;x<textureSpan-32;x+=2*stride)ctx.fillRect(base+x,0,stride,2048);
    ctx.fillRect(base+textureSpan-32,0,32,2048);
    const geometry=new T.PlaneGeometry((right-left)*scale,rowHeight*scale);
    const y=textureTop+(stride-1)*(rowHeight+2);
    geometry.translate(((right+left-r.width)/2)*scale,(r.height/2-y-rowHeight/2)*scale,0);
    const uv=geometry.getAttribute('uv');
    for(let i=0;i<uv.count;i++){
      const x=uv.getX(i), v=uv.getY(i);
      const shear=(1-v)*slope*rowHeight*textureSpan/(right-left);
      uv.setXY(i,(base+x*textureSpan-shear)/2048,.4+.1*v);
    }
    chart.add(new T.Mesh(geometry,printMaterial));
  }
  chart.updateMatrixWorld();
  v.model.attach(chart);
  v.invalidateTraceScene();
  const sizes=()=>({canvas:[v.renderer.domElement.width,v.renderer.domElement.height],
                    transmission_scale:v.renderer.transmissionResolutionScale});
  const p=window.sharpProbe={frames:[],motion:null}, present=v.pathTracing.showFrame;
  v.pathTracing.showFrame=function(bitmap,samples){
    present.call(this,bitmap,samples);
    p.frames.push({samples,ms:Math.round(performance.now()-p.start),bitmap:[bitmap.width,bitmap.height]});
  };
  const resize=v.resize;
  v.resize=function(...args){
    const result=resize.apply(this,args);
    if(this.motionQuality)p.motion=sizes();
    return result;
  };
  const dpr=devicePixelRatio;
  return {css:[r.width,r.height],device:[Math.round(r.width*dpr),Math.round(r.height*dpr)],dpr,
          left,right,top,widths,slope,rowHeight,edgeTop,edgeBottom,center,patchTop,patchBottom,
          textureTop,textureSpan,sourceTexture:[tex.width,tex.height]};
}"""


def measure_chart(image, chart):
    w,h=image.size
    sx,sy=np.asarray(chart['css'])/[w,h]
    luma=np.asarray(image,dtype=np.float32)@np.array([.2126,.7152,.0722],dtype=np.float32)
    x=(np.arange(w)+.5)*sx
    y=(np.arange(h)+.5)*sy
    left,right=chart['left']+12,chart['right']-12
    bands=(x>left)&(x<right)
    edge_rows=(y>chart['edgeTop']+3)&(y<chart['edgeBottom']-3)
    edge=luma[edge_rows][:,bands]
    d=x[bands][None,:]-chart['center']-chart['slope']*(y[edge_rows,None]-chart['edgeTop'])
    low,high=float(np.mean(edge[d < -15])),float(np.mean(edge[d > 15]))
    assert high-low>12,(low,high,'calibration edge missing')
    # Bin the slanted edge by distance from its mathematical centre. This
    # avoids counting a favourable sampling phase or a single aliased row.
    bin_width=.125
    bins=np.arange(-7,7+bin_width,bin_width)
    counts,_=np.histogram(d,bins)
    summed,_=np.histogram(d,bins,weights=np.asarray((edge-low)/(high-low),dtype=np.float64))
    keep=counts>0
    centers=(bins[:-1]+.5*bin_width)[keep]
    esf=np.maximum.accumulate(summed[keep]/counts[keep])
    assert esf[0]<.1 and esf[-1]>.9,(esf[0],esf[-1])
    crossings=np.interp([.1,.9],esf,centers)
    modulation={}
    for i,width in enumerate(chart['widths']):
        start=chart['top']+(chart['rowHeight']+2)*i
        rows=(y>start+3)&(y<start+chart['rowHeight']-3)
        phase=np.pi*(x[bands][None,:]-chart['left']-chart['slope']*(y[rows,None]-start))/width
        samples=(luma[rows][:,bands]-low)/(high-low)
        # The first Fourier harmonic of a 0..1 ideal square wave is 2/pi.
        wave=np.exp(-1j*phase)
        amplitude=2*abs(np.mean((samples-samples.mean())*wave))
        modulation[str(width)]=round(float(amplitude*np.pi/2),4)
    texture_modulation={}
    for stride in (1,2):
        y0=chart['textureTop']+(stride-1)*(chart['rowHeight']+2)
        rows=(y>y0+3)&(y<y0+chart['rowHeight']-3)
        region=luma[rows]
        u=(x[None,:]-chart['left']-chart['slope']*(y[rows,None]-y0))*chart['textureSpan']/(chart['right']-chart['left'])
        a=float(np.mean(region[(u>6)&(u<26)]))
        b=float(np.mean(region[(u>chart['textureSpan']-26)&(u<chart['textureSpan']-6)]))
        assert b-a>12,(a,b,'texture atlas missing')
        core=(u>45)&(u<chart['textureSpan']-45)
        signal=(region[core]-a)/(b-a)
        phase=u[core]*np.pi/stride
        texture_modulation[str(stride)]=round(float(np.pi*abs(np.mean((signal-signal.mean())*np.exp(-1j*phase)))),4)
    patch=luma[(y>chart['patchTop']+4)&(y<chart['patchBottom']-4)][:,bands]
    # Differencing neighbouring interior pixels rejects the studio's smooth
    # lighting gradient. The Gaussian-equivalent sigma is in display levels.
    differences=np.diff(patch,axis=1)
    noise=np.median(abs(differences-np.median(differences)))/(.67449*np.sqrt(2))
    # MAD alone quantizes to zero on a very clean 8-bit patch. Include RMS
    # after removing the local median gradient for these low-amplitude cases.
    residual=differences-np.median(differences,axis=1,keepdims=True)
    noise_rms=np.sqrt(np.mean(residual**2)/2)
    return dict(line_modulation=modulation,texture_texel_modulation=texture_modulation,
                edge_10_90_css_px=round(float(np.diff(crossings)[0]),3),
                edge_10_90_canvas_px=round(float(np.diff(crossings)[0]/sx),3),
                matte_adjacent_sigma_255=round(float(noise),3),matte_adjacent_rms_255=round(float(noise_rms),3),
                reference_gray_255=[round(low,1),round(high,1)])


def capture(page):
    encoded=page.evaluate("realtime.instance.captureMaterialFrame().toDataURL().split(',')[1]")
    return Image.open(io.BytesIO(base64.b64decode(encoded))).convert('RGB')


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    reports={}
    with project_server(backdrop='white') as (url,ids),sync_playwright() as pw:
        if args.old_modules:snapshot_routes(args.old_modules)
        prepare_exports([ids['base']])
        for dpr in args.dpr:
            with pw.chromium.launch(channel='chrome',headless=True,args=['--use-angle=d3d11','--enable-gpu']) as browser:
                page=browser.new_page(viewport={'width':1470,'height':860},device_scale_factor=dpr)
                page.add_init_script("localStorage.setItem('wuying-viewport-trace','off')")
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('console',lambda m:errors.append(m.text) if 'Shader Error' in m.text else None)
                page.goto(url)
                open_project(page,ids['base'])
                chart=page.evaluate(INSTALL_CHART)
                page.evaluate("""() => { sharpProbe.start=performance.now();
                    realtime.instance.setTraceOptions({enabled:true,limit:128}); }""")
                page.wait_for_function("""() => sharpProbe.frames.at(-1)?.samples===128 ||
                    realtime.instance.pathTracing.state.mode==='fallback'""",timeout=360000)
                state=page.evaluate('realtime.instance.pathTracing.state')
                assert state['samples']==128 and 'NVIDIA' in state['adapterName'],state
                frames=page.evaluate('sharpProbe.frames')
                assert frames[0]['samples']==1 and all(a['samples']<b['samples'] for a,b in zip(frames,frames[1:])),frames
                frame=capture(page)
                prefix='dpr-'+format(dpr,'g')
                frame.save(args.output/(prefix+'.png'))
                optics=measure_chart(frame,chart)
                if not args.old_modules and 1 <= dpr <= 2:
                    assert list(frame.size) == chart['device'] == frames[-1]['bitmap'], (chart, frame.size, frames[-1])
                    assert optics['line_modulation']['1'] > .10, optics
                    assert optics['texture_texel_modulation']['1'] > .10, optics
                    assert optics['edge_10_90_css_px'] < 2, optics
                    assert optics['matte_adjacent_rms_255'] < 2, optics
                print(prefix,'CSS / canvas / trace',chart['css'],frame.size,frames[-1]['bitmap'],optics,flush=True)
                page.evaluate('() => {sharpProbe.frames=[]; realtime.instance.requestDraw();}')
                page.wait_for_function('sharpProbe.frames.length>=2',timeout=90000)
                drag=measure(page)
                motion=page.evaluate('sharpProbe.motion')
                assert motion and motion['canvas'][0]>1,motion
                if not args.old_modules:
                    motion_width,motion_height=motion['canvas']
                    assert motion_width*motion_height<=800_000,motion
                    assert motion_width<=frame.width and motion_height<=frame.height,(motion,frame.size)
                    assert motion['transmission_scale']<=1,motion
                assert not errors,errors
                reports[prefix]=dict(chart=chart,canvas=frame.size,trace_bitmap=frames[-1]['bitmap'],
                    trace_ms=frames[-1]['ms'],first_ms=frames[0]['ms'],adapter=state['adapterName'],frames=frames,
                    sharpness=optics,drag=drag,motion=motion)
                (args.output/'metrics.json').write_text(json.dumps(reports,indent=2)+'\n',encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--old-modules',type=Path)
    parser.add_argument('--dpr',nargs='+',type=float,default=[1,2])
    run(parser.parse_args())

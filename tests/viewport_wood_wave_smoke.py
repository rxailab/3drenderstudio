"""Compare the production WebGL wood field with samples from catalog_wave_reference.py."""
import argparse
import base64
import json
import math
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def check(reference):
    rows = [r for r in json.loads(reference.read_text(encoding='utf-8'))['samples'] if r['type']=='wood']
    assert rows
    src = (ROOT/'static/viewport-wood-wave.js').read_bytes()
    module = 'data:text/javascript;base64,' + base64.b64encode(src).decode('ascii')
    with sync_playwright() as pw, pw.chromium.launch(channel='chrome', headless=True,
            args=['--enable-gpu','--use-angle=d3d11']) as browser:
        page = browser.new_page()
        results = page.evaluate("""async ({module,rows})=>{
          const {woodWaveGLSL}=await import(module);
          const gl=new OffscreenCanvas(1,1).getContext('webgl2',{powerPreference:'high-performance'});
          if(!gl?.getExtension('EXT_color_buffer_float'))throw Error('Floating-point WebGL2 required');
          const program=gl.createProgram();
          function shader(kind,source){
            const s=gl.createShader(kind);gl.shaderSource(s,source);gl.compileShader(s);
            if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));
            gl.attachShader(program,s);
          }
          shader(gl.VERTEX_SHADER,`#version 300 es
            void main(){gl_Position=vec4(0.,0.,0.,1.);gl_PointSize=1.;}`);
          shader(gl.FRAGMENT_SHADER,`#version 300 es
            precision highp float;
            precision highp int;
            uniform vec3 p;uniform vec2 params;out vec4 result;
            ${woodWaveGLSL}
            void main(){result=wyWoodWaveGradient(p,params.x,params.y);}`);
          gl.linkProgram(program);
          if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
          gl.useProgram(program);
          const target=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,target);
          gl.texStorage2D(gl.TEXTURE_2D,1,gl.RGBA32F,1,1);
          gl.bindFramebuffer(gl.FRAMEBUFFER,gl.createFramebuffer());
          gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,target,0);
          if(gl.checkFramebufferStatus(gl.FRAMEBUFFER)!==gl.FRAMEBUFFER_COMPLETE)throw Error('incomplete probe');
          const pos=gl.getUniformLocation(program,'p'), params=gl.getUniformLocation(program,'params');
          return rows.map(row=>{
            gl.uniform3fv(pos,row.xyz);gl.uniform2f(params,.5*Math.max(2,Math.min(220,.18/row.size)),row.rotation_degrees*Math.PI/180);
            gl.drawArrays(gl.POINTS,0,1);
            const out=new Float32Array(4);gl.readPixels(0,0,1,1,gl.RGBA,gl.FLOAT,out);
            if(gl.getError()!==gl.NO_ERROR)throw Error('WebGL probe failed');
            return Array.from(out);
          });
        }""", dict(module=module,rows=rows))
    value_error = max(abs(out[3]-row['value']) for out,row in zip(results,rows))
    gradient_error = max(abs(out[i]-row['gradient'][i]) for out,row in zip(results,rows) for i in range(3))
    assert math.isfinite(value_error) and value_error < .00003, value_error
    # Blender's reference gradients are central differences of float32 node values.
    assert math.isfinite(gradient_error) and gradient_error < .09, gradient_error
    print(f'{len(rows)} native samples; maximum value error {value_error:.7f}, gradient error {gradient_error:.5f}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference',type=Path)
    check(parser.parse_args().reference)

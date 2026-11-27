"""Check the moving glass shader against two exact Snell refractions at three camera angles."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


SCENE = """async () => {
  const T=await import('/static/vendor/three/build/three.module.js');
  const {FastViewportOptics}=await import('/static/viewport-fast-optics.js');
  const {RectAreaLightUniformsLib}=await import('/static/vendor/three/examples/jsm/lights/RectAreaLightUniformsLib.js');
  RectAreaLightUniformsLib.init();
  const renderer=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
  const side=180;
  renderer.setSize(side,side);
  const scene=new T.Scene(),model=new T.Group();
  scene.add(model);
  scene.background=new T.Color(.4,.4,.4);
  const material=new T.MeshPhysicalMaterial({transmission:1,ior:1.52,thickness:2,roughness:0});
  const ball=new T.Mesh(new T.SphereGeometry(1,96,64),material);
  model.add(ball);
  const stripes=new Uint8Array(24*4);
  for(let i=0;i<24;i++)stripes.set(i%2 ? [255,255,255,255] : [12,12,12,255],i*4);
  const texture=new T.DataTexture(stripes,24,1);
  texture.needsUpdate=true;
  const panel=new T.Mesh(new T.PlaneGeometry(6,6),new T.MeshBasicMaterial({map:texture}));
  panel.geometry.computeBoundingBox();panel.position.z=-1.7;model.add(panel);
  const optics=new FastViewportOptics({model,scene,renderer,bounds:{diagonal:9}});
  optics.prepare();
  const camera=new T.OrthographicCamera(-1.28,1.28,1.28,-1.28,.1,15);
  const target=new T.Sphere(new T.Vector3(),1),caster=new T.Raycaster(),gl=renderer.getContext();
  const pixels=new Uint8Array(side*side*4);
  const render=()=>{
    renderer.render(scene,camera);
    gl.readPixels(0,0,side,side,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
  };
  const bend=(direction,normal,eta)=>{
    const cos=-direction.dot(normal),k=1-eta*eta*(1-cos*cos);
    return direction.clone().multiplyScalar(eta).addScaledVector(normal,eta*cos-Math.sqrt(k)).normalize();
  };
  const results={};
  for(const degrees of [-24,0,24]){
    const angle=degrees*Math.PI/180;
    camera.position.set(5*Math.sin(angle),.35,5*Math.cos(angle));camera.lookAt(0,0,0);camera.updateMatrixWorld();
    render();
    let checked=0,correct=0;
    for(let y=32;y<side-32;y+=2)for(let x=32;x<side-32;x+=2){
      if((x-side/2)**2+(y-side/2)**2>45**2)continue;
      caster.setFromCamera({x:(x+.5)/side*2-1,y:(y+.5)/side*2-1},camera);
      const enter=caster.ray.intersectSphere(target,new T.Vector3());
      const inside=bend(caster.ray.direction,enter,1/material.ior);
      const exit=enter.clone().addScaledVector(inside,-2*enter.dot(inside));
      const outgoing=bend(inside,exit.clone().negate(),material.ior);
      const t=(panel.position.z-exit.z)/outgoing.z;
      const hitX=exit.x+t*outgoing.x, coordinate=(hitX+3)*4;
      if(t<=0 || coordinate<0 || coordinate>=24 || Math.abs(coordinate-Math.round(coordinate))<.12)continue;
      const expected=Boolean(Math.floor(coordinate)%2);
      if((pixels[4*(x+y*side)]>160)===expected)correct++;
      checked++;
    }
    results[degrees]={checked,agreement:correct/checked};
  }
  // These variants exercise separate GLSL code paths. Keep the highlight
  // visible as well as checking both shader compilation and framebuffer state.
  const lamp=new T.RectAreaLight(0xffffff,5,3,2);
  lamp.position.set(.5,2,3);lamp.lookAt(0,0,0);scene.add(lamp);
  for(const [roughness,dispersion,thickness] of [[.3,.25,2],[.6,0,2],[0,0,0]]){
    material.roughness=roughness;material.dispersion=dispersion;material.thickness=thickness;
    material.userData.wuyingTrace={thinWalled:thickness===0};
    material.needsUpdate=true;optics.refreshBindings();optics.prepare();render();
    if(gl.getError()!==gl.NO_ERROR || pixels[4*(side/2+side/2*side)]===0)throw Error('Invalid glass variant');
  }
  renderer.dispose();optics.dispose();
  return results;
}"""


def main():
    handler = partial(QuietFiles, directory=str(ROOT))
    with ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright, playwright.chromium.launch(
                channel='chrome', args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader']
            ) as browser:
                page = browser.new_page()
                errors = []
                page.on('console', lambda msg: errors.append(msg.text) if 'Shader Error' in msg.text else None)
                page.route('**/optics-angle-fixture', lambda route: route.fulfill(content_type='text/html',
                    body='<script type="importmap">{"imports":{"three":"/static/vendor/three/build/three.module.js"}}</script>'))
                page.goto(f'http://127.0.0.1:{server.server_port}/optics-angle-fixture')
                results = page.evaluate(SCENE)
                assert not errors, errors
                print(results)
                assert all(r['checked'] > 400 and r['agreement'] > .94 for r in results.values()), results
        finally:
            server.shutdown()
            thread.join()


if __name__ == '__main__':
    main()

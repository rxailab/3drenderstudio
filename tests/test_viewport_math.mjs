import test from 'node:test';
import assert from 'node:assert/strict';
import { blenderToGltf,gltfToBlender,cameraFromShot,shotFromCamera,fittedOrthoScale,aspectNumber } from '../static/viewport-math.mjs';
import * as THREE from '../static/vendor/three/build/three.module.js';
import {readFile} from 'node:fs/promises';
const bounds={min:[-1,0,-.4],max:[1,3,.8],center:[0,1.5,.2],diagonal:Math.hypot(2,3,1.2)};
const near=(a,b,message='')=>assert.ok(Math.abs(a-b)<1e-8,message || `${a} != ${b}`);
test('Blender/glTF axes are invertible and preserve dimensional offsets',()=>{
  for(const point of [[0,0,0],[2,-3,4],[-.003,12.8,-500]])assert.deepEqual(gltfToBlender(blenderToGltf(point)),point);
  assert.deepEqual(blenderToGltf([0,-1,0]),[0,0,1]);
});
test('orthographic orbit, horizontal zoom and pan round-trip over all sides and aspects',()=>{
  for(const aspect of [1,2,.5,16/9])for(const azimuth of [-179,-90,0,35,90,179])for(const elevation of [-60,18,80]){
    const shot={projection:'orthographic',azimuth,elevation,ortho_scale:5.7,target_offset:[.12,-.04,.08],focal_length:70};
    const state=cameraFromShot(shot,bounds,aspect);
    const result=shotFromCamera({...state,zoom:1},bounds);
    near(result.azimuth,azimuth);near(result.elevation,elevation);near(result.ortho_scale,5.7);
    result.target_offset.forEach((v,i)=>near(v,shot.target_offset[i]));near(state.width/state.height,aspect);
    const zoomed=shotFromCamera({...state,zoom:2},bounds);near(zoomed.ortho_scale,2.85);
    assert.ok(!('focal_length' in result),'orthographic zoom must not masquerade as focal length');
  }
});
test('saved orthographic span exactly matches Three projection across portrait and landscape',()=>{
  for(const aspect of [.5,1,2]){
    const state=cameraFromShot({azimuth:37,elevation:21,ortho_scale:4,target_offset:[.1,-.2,.05]},bounds,aspect);
    const camera=new THREE.OrthographicCamera(-state.width/2,state.width/2,state.height/2,-state.height/2,state.near,state.far);
    camera.position.fromArray(state.position);camera.up.set(0,1,0);camera.lookAt(...state.target);camera.updateMatrixWorld(true);
    const center=new THREE.Vector3(...state.target).project(camera);near(center.x,0);near(center.y,0);
    const right=new THREE.Vector3(1,0,0).applyQuaternion(camera.quaternion).multiplyScalar(state.width/2).add(new THREE.Vector3(...state.target)).project(camera);
    near(right.x,1);near(right.y,0);
    const top=new THREE.Vector3(0,1,0).applyQuaternion(camera.quaternion).multiplyScalar(state.height/2).add(new THREE.Vector3(...state.target)).project(camera);
    near(top.x,0);near(top.y,1);
  }
});
test('automatic ortho fit contains all bounding corners with a real margin',()=>{
  for(const aspect of [.5,1,2])for(const azimuth of [-60,0,120]){
    const state=cameraFromShot({azimuth,elevation:25},bounds,aspect,{},.1);
    const camera=new THREE.OrthographicCamera(-state.width/2,state.width/2,state.height/2,-state.height/2,state.near,state.far);
    camera.position.fromArray(state.position);camera.lookAt(...state.target);camera.updateMatrixWorld(true);
    for(const x of [bounds.min[0],bounds.max[0]])for(const y of [bounds.min[1],bounds.max[1]])for(const z of [bounds.min[2],bounds.max[2]]){
      const p=new THREE.Vector3(x,y,z).project(camera);assert.ok(Math.abs(p.x)<=.800001 && Math.abs(p.y)<=.800001);
    }
  }
});
test('camera source scale is reused only for unchanged framing; explicit scale stays authoritative',()=>{
  const source={projection:'orthographic',azimuth:35,elevation:18,aspect:1,ortho_scale:100};
  near(cameraFromShot({azimuth:35,elevation:18},bounds,1,source).width,100);
  assert.notEqual(cameraFromShot({azimuth:60,elevation:18},bounds,1,source).width,100);
  assert.notEqual(cameraFromShot({azimuth:35,elevation:18},bounds,2,source).width,100);
  near(cameraFromShot({azimuth:60,elevation:18,ortho_scale:7},bounds,2,source).width,7);
});
test('perspective dolly persists as camera distance and honors imported sensor width',()=>{
  const shot={projection:'perspective',azimuth:-50,elevation:12,focal_length:80,camera_distance:12,target_offset:[.03,.02,-.04]};
  const state=cameraFromShot(shot,bounds,2,{sensor_width:48});
  near(state.fov,2*Math.atan(48/(2*80*2))*180/Math.PI);
  const result=shotFromCamera(state,bounds);near(result.camera_distance,12);near(result.focal_length,80);
  near(result.azimuth,-50);near(result.elevation,12);assert.ok(!('ortho_scale' in result));
});
test('invalid or excessively displaced camera states fail instead of serializing broken plans',()=>{
  assert.throws(()=>cameraFromShot({}, {...bounds,diagonal:0}),/范围/);
  assert.throws(()=>shotFromCamera({position:[0,1,2],target:[Infinity,0,0]},bounds),/视角数据/);
  assert.throws(()=>shotFromCamera({position:[100,101,100],target:[100,100,100],width:1},bounds),/太远/);
  assert.throws(()=>shotFromCamera({position:[0,2,1],target:[0,1,0],width:0},bounds),/缩放/);
  assert.equal(aspectNumber('4:5'),.8);assert.equal(aspectNumber('invalid'),1);
});
async function productViewportClass() {
  // Execute the shipped module without a browser/GPU. Import URLs follow the
  // bundled import map. This does not claim any browser pixel verification.
  const moduleURL=new URL('../static/viewport.js',import.meta.url);
  let source=await readFile(moduleURL,'utf8');
  source=source.replace(/from '([^']+)'/g,(all,name)=>`from '${name==='three'?new URL('../static/vendor/three/build/three.module.js',import.meta.url).href:new URL(name,moduleURL).href}'`);
  const previous=globalThis.window;globalThis.window={dispatchEvent(){}};
  let ProductViewport;
  try{({ProductViewport}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64')));}finally{if(previous===undefined)delete globalThis.window;else globalThis.window=previous;}
  return ProductViewport;
}
test('real viewport lighting preserves global power, world intensity and exposure changes',async()=>{
  const ProductViewport=await productViewportClass();
  const build=(power,world,exposure)=>{
    const viewport=Object.create(ProductViewport.prototype);viewport.bounds=bounds;viewport.scene=new THREE.Scene();viewport.renderer={};
    viewport.buildLighting({world:{strength:world},exposure,lights:[{type:'AREA',position:[-3,5,5],direction:[0,-1,-1],color:[1,1,1],energy:power},{type:'AREA',position:[3,3,3],color:[1,1,1],energy:power*.4}]});
    return viewport;
  };
  const dim=build(500,1,0),bright=build(1000,2,1);
  // Typical studio scenes have a low world strength; keep their unlit sides
  // readable without discarding scene brightness and exposure controls.
  const studio=build(358,.14,0);
  const fill=studio.scene.children.find(node=>node.isHemisphereLight);
  assert.ok(fill && fill.intensity>=.75);
  assert.ok(studio.scene.environmentIntensity>=.45);
  // The interactive camera-side fill is separate from the exported Blender
  // lights: scene light power must scale independently of that bounded fill.
  const a=dim.scene.children.filter(node=>node.isDirectionalLight && node!==dim.viewFill),b=bright.scene.children.filter(node=>node.isDirectionalLight && node!==bright.viewFill);
  assert.equal(a.length,2);a.forEach((light,i)=>near(b[i].intensity/light.intensity,2));
  assert.ok(dim.viewFill?.isDirectionalLight && bright.viewFill?.isDirectionalLight);
  near(build(1000,1,0).viewFill.intensity,dim.viewFill.intensity);
  assert.ok(bright.viewFill.intensity>dim.viewFill.intensity);
  assert.ok(build(500,100,0).viewFill.intensity<=1.4);
  assert.ok(bright.scene.environmentIntensity>dim.scene.environmentIntensity);near(bright.renderer.toneMappingExposure/dim.renderer.toneMappingExposure,2);
  assert.ok(dim.scene.children.some(node=>node.isMesh && node.receiveShadow && node.material.isShadowMaterial),'white preview must retain a real shadow receiver');
});
test('damped control change events schedule one tracked RAF and stop when hidden',async()=>{
  const ProductViewport=await productViewportClass();
  const prior=Object.fromEntries(['document','requestAnimationFrame','cancelAnimationFrame'].map(key=>[key,globalThis[key]]));
  const queued=new Map();let nextId=0,updates=0,renders=0;
  globalThis.document={hidden:false};globalThis.requestAnimationFrame=callback=>{const id=++nextId;queued.set(id,callback);return id;};globalThis.cancelAnimationFrame=id=>queued.delete(id);
  try{
    const viewport=Object.create(ProductViewport.prototype);Object.assign(viewport,{visible:true,disposed:false,camera:{},scene:{},renderer:{render(){renders++;}}});
    viewport.controls={update(){updates++;if(updates<=4){viewport.requestDraw();return true;}return false;}};
    viewport.requestDraw();viewport.requestDraw();assert.equal(queued.size,1,'repeated input before a frame must coalesce');
    const step=()=>{const [id,callback]=queued.entries().next().value;queued.delete(id);callback();assert.ok(queued.size<=1,'change during update must not create a duplicate RAF');assert.equal(viewport.frameRequest,queued.size?[...queued.keys()][0]:0,'every queued frame must remain tracked');};
    for(let i=0;i<5;i++)step();
    assert.equal(updates,5);assert.equal(renders,4);assert.equal(queued.size,0,'damping completion must stop scheduling');
    viewport.requestDraw();viewport.setVisible(false);assert.equal(queued.size,0,'hiding cancels the only queued frame');
    viewport.visible=true;viewport.controls.update=()=>{viewport.requestDraw();viewport.visible=false;return true;};viewport.requestDraw();step();
    assert.equal(queued.size,0,'hiding during a synchronous change must not resurrect the loop');assert.equal(renders,4);
  }finally{for(const [key,value] of Object.entries(prior)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}
});

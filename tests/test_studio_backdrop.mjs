import assert from 'node:assert/strict';
import test from 'node:test';
import { backdropLighting, backdropRadiance, fitBackdrop } from '../static/viewport-backdrop.js';
import * as THREE from '../static/vendor/three/build/three.module.js';
import { buildStudioLighting } from '../static/viewport-studio.js';
import { addStudioLight } from '../static/viewport-light.js';

test('photographic crop covers portrait and landscape cameras without stretching',()=>{
  const texture=new THREE.Texture({width:2000,height:1000});
  fitBackdrop(texture,1);
  assert.deepEqual(texture.repeat.toArray(),[.5,1]);
  assert.deepEqual(texture.offset.toArray(),[.25,0]);
  fitBackdrop(texture,4);
  assert.deepEqual(texture.repeat.toArray(),[1,.5]);
  assert.deepEqual(texture.offset.toArray(),[0,.25]);
});

test('measured HDR preserves pole orientation, archive gain and rotation independent of card yaw',()=>{
  const image=new Float32Array(128*64*3);
  for(let y=0;y<64;y++)for(let x=0;x<128;x++){
    const i=3*(y*128+x);
    image[i]=y<32?2:1;image[i+1]=x<32?4:1;image[i+2]=1;
  }
  const build=(angle,gain)=>backdropRadiance({lighting:{key:0,fill:0,rim:0,yaw:73},environmentRotation:angle,environmentGain:gain},null,null,image);
  const tex=build(0,1), turned=build(90,.5);
  const rgb=(t,x,y)=>Array.from(t.image.data.subarray(4*(y*256+x),4*(y*256+x)+3));
  assert.deepEqual(rgb(tex,0,12),[1,4,1]);
  assert.deepEqual(rgb(tex,0,100),[2,4,1]);
  assert.deepEqual(rgb(turned,0,12),[.5,.5,.5]);
  tex.dispose();turned.dispose();
});

test('presets keep source scene metadata and apply independent key, fill, rim and world settings',()=>{
  const scene={world:{strength:.5},lights:[{name:'Key',type:'AREA',energy:4,position:[1,0,0],direction:[-1,0,0]}]};
  const bounds={center:[0,0,0],diagonal:3};
  assert.equal(backdropLighting(scene,bounds,{id:'white'}),scene);
  const preset={id:'test',lighting:{key:2,fill:.5,rim:2,ambient:.7,yaw:90},groundShadow:0};
  const rig=backdropLighting(scene,bounds,preset);
  assert.equal(rig.lights[0].energy,8);
  assert.ok(Math.abs(rig.lights[0].position[2]+1)<1e-8);
  assert.equal(rig.world.strength,.35);
  assert.equal(rig.groundShadow,0);
  assert.deepEqual(scene.lights[0].position,[1,0,0]);
  const cologne=backdropLighting({},bounds,{...preset,id:'cologne'});
  assert.deepEqual(cologne.lights.map(light=>light.name),['Top','Highlight','Label','Label Logo']);
  assert.equal(cologne.lights[0].role,'key');
  assert.equal(backdropLighting({},bounds,preset).lights.length,3);
});

test('teaching scenes use the same measured lamp types in the interactive and tracing stages',()=>{
  const layouts={
    'blue-keyboard':['AREA','AREA'],'amber-caustics':['SPOT'],
    'silver-beam':['AREA','AREA','AREA','AREA'],'charcoal-stage':['AREA','AREA','SPOT'],
    'silver-studio':['AREA','AREA','SPOT'],'black-jewelry':['AREA','AREA'],
    'wood-interior':['AREA','SPOT'],'charcoal-headphones':['SPOT'],'dark-garage':['AREA','SPOT'],
    'cafe-window':['AREA','AREA'],'silver-table':['AREA'],'blue-night':['SPOT','AREA'],welcome:[],
  };
  const bounds={center:[1,2,3],diagonal:3},source={lights:[{type:'AREA',energy:80,position:[2,3,4]}],world:{strength:1}};
  for (const [id,types] of Object.entries(layouts)){
    const metadata=backdropLighting(source,bounds,{id,groundShadow:0});
    assert.deepEqual(metadata.lights.map(s=>s.type),types,id);
    const traced=new THREE.Scene();
    for (const lamp of metadata.lights) addStudioLight(traced,lamp,bounds);
    const view={bounds,scene:new THREE.Scene(),renderer:{}};
    buildStudioLighting(view,metadata);
    const lamps=scene=>scene.children.filter(s=>s.isRectAreaLight || s.isSpotLight);
    const interactive=lamps(view.scene),pathLights=lamps(traced);
    assert.ok(!view.scene.children.some(s=>s.visible && (s.isHemisphereLight || s===view.viewFill)),id);
    assert.equal(interactive.length,types.length,id);
    assert.deepEqual(interactive.map(s=>[s.name,s.type,s.intensity,s.position.toArray(),s.color.toArray()]),
      pathLights.map(s=>[s.name,s.type,s.intensity,s.position.toArray(),s.color.toArray()]),id);
    if(id==='welcome')assert.equal(view.scene.children.filter(s=>s.isDirectionalLight).length,1);
    if(id==='blue-night')assert.ok(interactive[0].color.b>interactive[0].color.r && interactive[1].color.r>interactive[1].color.b);
    if(id==='silver-beam')assert.ok(metadata.lights[2].height_world > 6*metadata.lights[2].width_world);
  }
  const keyboard=backdropLighting(source,bounds,{id:'blue-keyboard'}).lights;
  assert.ok(Math.abs(keyboard[0].energy/keyboard[1].energy-700/240)<1e-10);
  const larger=backdropLighting(source,{...bounds,diagonal:6},{id:'blue-keyboard'}).lights;
  assert.equal(larger[0].energy,keyboard[0].energy*4);
  assert.equal(larger[0].width_world,keyboard[0].width_world*2);
});

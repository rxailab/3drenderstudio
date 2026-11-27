import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from '../static/vendor/three/build/three.module.js';
import {serializeTraceScene,buildTraceScene} from '../static/viewport-trace-scene.js';

const bounds={min:[-1,-1,-1],max:[1,1,1],center:[0,0,0],diagonal:Math.sqrt(12)};
function mapOn(channel) {
  const texture=new THREE.DataTexture(new Uint8Array([42,84,127,255]),1,1);
  texture.channel=channel;
  return texture;
}
function source(material) {
  const geometry=new THREE.PlaneGeometry(1,1);
  geometry.setAttribute('uv1',new THREE.Uint16BufferAttribute([0,12345,60000,17,15535,65535,20100,31415],2,true));
  geometry.setAttribute('uv2',new THREE.Float32BufferAttribute([2,3,4,5,6,7,8,9],2));
  geometry.setAttribute('uv3',new THREE.Float32BufferAttribute([9,8,7,6,5,4,3,2],2));
  return new THREE.Mesh(geometry,material);
}
function coordinates(geometry,channel) {
  const attribute=geometry.getAttribute(channel?'uv'+channel:'uv');
  return Array.from({length:attribute.count},(_,i)=>[attribute.getX(i),attribute.getY(i)]);
}
function sameCoordinates(actual,expected) {
  assert.equal(actual.length,expected.length);
  for(let i=0;i<actual.length;i++)for(let j=0;j<2;j++)assert.ok(Math.abs(actual[i][j]-expected[i][j])<1e-6);
}

test('a glTF material selecting TEXCOORD_1 for all maps traces on its authored UVs using one layer',async()=>{
  const packed=mapOn(1);
  const model=source(new THREE.MeshStandardMaterial({map:mapOn(1),normalMap:mapOn(1),metalnessMap:packed,roughnessMap:packed}));
  const {payload}=await serializeTraceScene(model,{},bounds);
  assert.deepEqual(payload.uvChannels,[1]);
  assert.equal(payload.geometries[0].attrs.uv,undefined);
  const trace=buildTraceScene(payload),mesh=trace.scene.children[0];
  try {
    assert.deepEqual(trace.uvAttributes,['uv']);
    for(const field of ['map','normalMap','metalnessMap','roughnessMap']){
      assert.equal(mesh.material[field].channel,0);
      sameCoordinates(coordinates(mesh.geometry,mesh.material[field].channel),coordinates(model.geometry,1));
      assert.equal(model.material[field].channel,1);
    }
    assert.equal(mesh.material.roughnessMap,mesh.material.metalnessMap);
  } finally {trace.dispose();}
});

test('mixed per-map UV channels preserve the selected coordinates and an explicit texture transform',async()=>{
  const albedo=mapOn(2);
  albedo.matrixAutoUpdate=false;
  albedo.matrix.set(2,0,.25,0,-1,.9,0,0,1);
  const model=source(new THREE.MeshPhysicalMaterial({map:albedo,normalMap:mapOn(1),roughnessMap:mapOn(3),metalnessMap:mapOn(0)}));
  const {payload}=await serializeTraceScene(model,{},bounds);
  const trace=buildTraceScene(payload),mesh=trace.scene.children[0];
  try {
    assert.deepEqual(trace.uvAttributes,['uv','uv1','uv2','uv3']);
    for(const field of ['map','normalMap','roughnessMap','metalnessMap']){
      const original=model.material[field],restored=mesh.material[field];
      sameCoordinates(coordinates(mesh.geometry,restored.channel),coordinates(model.geometry,original.channel));
      assert.deepEqual(restored.matrix.elements,original.matrix.elements);
    }
    assert.equal(mesh.material.map.matrixAutoUpdate,false);
  } finally {trace.dispose();}
});

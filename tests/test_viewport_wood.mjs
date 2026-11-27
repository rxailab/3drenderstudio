import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from '../static/vendor/three/build/three.module.js';
import {assignGeneratedSpaces,localToGenerated,woodParameters} from '../static/viewport-wood-material.js';
import {makeLibraryPreviewMaterial} from '../static/viewport-library-material.js';
import {buildTraceScene,serializeTraceScene} from '../static/viewport-trace-scene.js';
import {SelectionMaterials} from '../static/viewport-selection.js';
import {cloneViewportMaterial,FastViewportOptics} from '../static/viewport-fast-optics.js';

const colors=[[.122499,.0379644,.0110323],[.122499,.0428779,.0110323]];
const oak={name:'Dark Stained Oak',pbr:{procedural:{type:'wood',size:5,colors},roughness_range:[0,1],
  color:colors[0],roughness:.5,metallic:0,transmission:0,coat:0,ior:1.5}};
const bounds={center:[0,0,0],min:[-10,-10,-10],max:[10,10,10],diagonal:Math.sqrt(1200)};
function samePosition(a,b){
  assert.ok(a.distanceTo(b)<1e-6,`${a.toArray()} != ${b.toArray()}`);
}

test('old exports derive one local Generated box for all primitives, including reversed Blender Y',()=>{
  const group=new THREE.Group();
  const left=new THREE.Mesh(new THREE.BoxGeometry(2,4,6).translate(-1,0,0));
  const right=new THREE.Mesh(new THREE.BoxGeometry(4,4,6).translate(2,0,0));
  for(const mesh of [left,right]){mesh.userData.productObjectName='Grip';group.add(mesh);}
  right.position.set(2,1,0); // local texspace is independent of the glTF node transform
  assignGeneratedSpaces(group);
  const space=left.userData.generatedSpace;
  assert.equal(space,right.userData.generatedSpace);
  assert.deepEqual(space,{center:[1,-0,0],half_size:[3,3,2]});
  const matrix=localToGenerated(space);
  samePosition(new THREE.Vector3(-2,-2,3).applyMatrix4(matrix),new THREE.Vector3());
  samePosition(new THREE.Vector3(4,2,-3).applyMatrix4(matrix),new THREE.Vector3(1,1,1));
  const custom={center:[7,5,4],half_size:[.2,.5,.4]};
  assignGeneratedSpaces(group,{Grip:custom});
  assert.equal(left.userData.generatedSpace,custom);
  assert.equal(right.userData.generatedSpace,custom);
});

test('wood uses the catalog color, roughness and height signal across UV layouts and transformed instances',async()=>{
  const group=new THREE.Group(),surface=makeLibraryPreviewMaterial(oak,()=>{},1);
  const sample=new THREE.Vector3(.06,.002,.008);
  const space={center:[.050312,-.0116566,0],half_size:[.0431323,.00897211,.00892938]};
  const expected=sample.clone().applyMatrix4(localToGenerated(space));
  for(const channel of [null,'uv','uv1']){
    const geom=new THREE.BoxGeometry(.086,.018,.018);
    if(channel==='uv1')geom.setAttribute(channel,geom.getAttribute('uv'));
    if(channel!=='uv')geom.deleteAttribute('uv');
    const mat=surface.forGeometry(geom,space);
    const wood=mat.userData.wuyingTrace.wood;
    assert.equal(wood.scale,1);
    assert.deepEqual(wood.colors,colors);
    assert.deepEqual(wood.range,[0,1]);
    assert.equal(wood.bumpDistance,.0006);
    assert.equal(mat.clearcoatRoughness,.03);
    for(const key of ['map','roughnessMap','bumpMap'])assert.equal(mat[key],null);
    const mesh=new THREE.Mesh(geom,mat);
    mesh.position.set(group.children.length,2,-1);
    mesh.rotation.set(.2,.8,.9);
    mesh.scale.set(1,2,.3);
    group.add(mesh);
  }
  const {payload}=await serializeTraceScene(group,{},bounds);
  assert.equal(payload.textures.length,0);
  const trace=buildTraceScene(payload);
  try{
    for(const [index,mesh] of group.children.entries()){
      const hit=sample.clone().applyMatrix4(mesh.matrixWorld);
      const traceMat=trace.scene.children[index].material;
      samePosition(hit.applyMatrix4(new THREE.Matrix4().fromArray(traceMat.userData.wuyingWood.worldToGenerated)),expected);
      assert.deepEqual(traceMat.userData.wuyingWood.range,[0,1]);
    }
  } finally{trace.dispose();surface.dispose();}
});

test('explicit channels take precedence and bump-only wood keeps the base material',()=>{
  const geom=new THREE.BoxGeometry();
  const pbr={...oak.pbr,textures:{base_color:'color.jpg',roughness:'roughness.jpg',height:'height.jpg'}};
  const inputs=woodParameters(pbr,{},geom);
  assert.equal(inputs.colors,null);assert.equal(inputs.range,null);assert.equal(inputs.bumpDistance,0);
  const overrides=woodParameters(oak.pbr,{color:'#336699',roughness:0},geom);
  assert.equal(overrides.colors,null);assert.equal(overrides.range,null);assert.equal(overrides.bumpDistance,.0006);
  const rings=woodParameters({...oak.pbr,roughness_range:null,procedural:{type:'wood',size:.004,rotation:90}}, {},geom);
  assert.equal(rings.colors,null);assert.equal(rings.range,null);assert.equal(rings.scale,22.5);
  assert.equal(rings.rotation,Math.PI/2);
});

test('selecting, reflecting and restoring the grip preserves the catalog shader and authored emission',()=>{
  const surface=makeLibraryPreviewMaterial(oak,()=>{},1);
  const geometry=new THREE.BoxGeometry(),wood=surface.forGeometry(geometry),selection=new SelectionMaterials();
  const emission=wood.emissive.clone(),baseKey=wood.customProgramCacheKey();
  // A coated wood has already been wrapped by the interactive reflection pass.
  wood.clearcoat=1;
  const model=new THREE.Mesh(geometry,wood),optics=new FastViewportOptics({model,bounds});
  const probe=new THREE.Texture();
  optics.probes.push({target:{texture:probe,dispose:()=>probe.dispose()},position:new THREE.Vector3()});
  optics.sceneChanged=false;optics.prepare();
  const opticsKey=wood.customProgramCacheKey();
  assert.notEqual(opticsKey,baseKey);
  try{
    const selected=selection.resolve(wood,true);
    assert.notEqual(selected,wood);
    assert.equal(selected.emissiveIntensity,.45);
    assert.equal(selected.customProgramCacheKey(),baseKey);
    model.material=selected;optics.refreshBindings();optics.prepare();
    assert.equal(selected.customProgramCacheKey(),opticsKey);
    const proxy=cloneViewportMaterial(selected);
    assert.equal(proxy.customProgramCacheKey(),baseKey); // no recursive probe sampling
    for(const material of [selected,proxy]){
      const shader={uniforms:{},vertexShader:'#include <begin_vertex>',fragmentShader:'#include <map_fragment>'};
      material.onBeforeCompile(shader);
      assert.ok(shader.fragmentShader.includes('wyWoodWaveGradient(wyGenerated,'));
    }
    proxy.dispose();
    assert.equal(selection.resolve(wood,false),wood); // another mesh shares this material
    assert.equal(selection.resolve(selected,false),wood);
    assert.ok(wood.emissive.equals(emission));
    assert.equal(wood.emissiveIntensity,0);
    wood.map=new THREE.Texture();wood.needsUpdate=true;
    assert.equal(selection.resolve(wood,true).map,wood.map);
    wood.map.dispose();
  }finally{selection.dispose();surface.dispose();optics.dispose();geometry.dispose();}
});

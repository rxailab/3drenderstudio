import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {traceKernelFeatures, validKernelFeatures} from '../static/viewport-trace-kernel.js';

const source=await readFile(new URL('../static/viewport-trace-worker.js',import.meta.url),'utf8');
const optical=traceKernelFeatures([{transmission:1,roughness:.008}]);

function fixture() {
  const messages=[], jobs=[], shared=[], discarded=[], timers=new Map();
  const programs=new WeakMap();
  class Material {
    constructor(params) { Object.assign(this,params); }
    dispose() { discarded.push(this);programs.delete(this); }
  }
  class Mesh {
    constructor(geometry,material) { this.geometry=geometry;this.material=material; }
  }
  const pass={_fsQuad:{_mesh:{geometry:{}}},_primaryTarget:{},_blendTargets:[{}, {texture:{}}],
    _blendQuad:{_mesh:{material:{}}},
    material:{vertexShader:'v',defines:{CAMERA_TYPE:1,FEATURE_DOF:0,FEATURE_BACKGROUND_MAP:0,FEATURE_FOG:0,FEATURE_WUYING_WOOD:0}}};
  const output={material:{map:null}};
  output._mesh={material:output.material};
  const backend={
    getRenderTarget() { return this.target; },setRenderTarget(t) { this.target=t; },
    properties:{get:m=>programs.get(m)},
    compileAsync(mesh) {
      if (mesh===pass._blendQuad._mesh || mesh===output._mesh) {
        shared.push({target:this.target,mesh,map:mesh.material.map});
        return Promise.resolve();
      }
      assert.equal(this.target,pass._primaryTarget);
      const entry={currentProgram:{program:{}}};
      programs.set(mesh.material,entry);
      return new Promise((resolve,reject)=>jobs.push({resolve,reject,material:mesh.material}));
    },
  };
  const api=runInNewContext(source.replace(/^import .*$/gm,'')+`
    tracer={_pathTracer:pass,_quad:output};gl=driver;renderer=backend;
    ({prewarm,stopWarmup,kernelProgram,profileFor,validProfile})
  `,{
    pass,output,backend,driver:{getExtension:()=>true,getProgramParameter:()=>true},
    traceKernelFeatures,validKernelFeatures,kernelForFeatures:(_,mask)=>'fragment:'+mask,
    THREE:{ShaderMaterial:Material,Mesh,PerspectiveCamera:class{},NoBlending:0},
    self:{addEventListener(){},postMessage:m=>messages.push(m)},
    setTimeout:fn=>{const id={};timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),
  });
  return {api,pass,output,jobs,shared,discarded,timers,messages};
}

test('a foreground run can claim the exact in-flight optical program and suppress the next warmup', async()=>{
  const {api,jobs,timers,messages}=fixture();
  const warm=api.prewarm({version:-1,features:-1});
  const profile=api.profileFor(optical,true,false);
  const foreground=api.kernelProgram(profile,false);
  api.stopWarmup();
  assert.equal(jobs.length,1);
  assert.equal(foreground.cache,'pending');
  jobs[0].resolve();
  await Promise.all([warm,foreground.entry.promise]);
  assert.equal(foreground.entry.ready,true);
  assert.equal(messages.length,0);
  assert.equal(timers.size,0);
});

test('restore the previous real feature set and distinguish projection and plate programs', async()=>{
  const {api,pass,output,jobs,shared,timers,messages}=fixture();
  const profile=api.profileFor(13,false,true);
  const warm=api.prewarm(profile);
  jobs[0].resolve();
  await warm;
  assert.equal(messages[0].kind,'warmed');
  assert.equal(messages[0].profile.features,13);
  assert.equal(shared.length,2);
  assert.equal(shared[0].target,pass._blendTargets[1]);
  assert.equal(shared[1].target,null);
  assert.equal(shared[1].map,pass._blendTargets[1].texture);
  assert.equal(output.material.map,null);
  assert.equal(timers.size,1);
  api.stopWarmup();
  pass.material.defines.CAMERA_TYPE=0;
  pass.material.defines.FEATURE_BACKGROUND_MAP=1;
  assert.equal(api.kernelProgram(profile,false).cache,'prewarmed');
  pass.material.defines.CAMERA_TYPE=1;
  assert.equal(api.kernelProgram(api.profileFor(13,true,true),false).cache,'miss');
  assert.equal(jobs.length,2);
});

test('evict linked programs and retry failed speculative compilation without poisoning real runs', async()=>{
  const {api,jobs,discarded,messages}=fixture();
  for (let mask=0;mask<5;mask++) {
    const {entry}=api.kernelProgram(api.profileFor(mask,true,false),false);
    jobs.at(-1).resolve();
    await entry.promise;
  }
  assert.deepEqual(discarded,[jobs[0].material]);
  const profile=api.profileFor(4,true,true);
  const warm=api.prewarm(profile);
  jobs.at(-1).reject(new Error('driver'));
  await warm;
  assert.equal(messages.at(-1).status,'failed');
  assert.equal(api.kernelProgram(profile,true).cache,'miss');
});

test('the procedural wood program can be prewarmed from a previous model',async()=>{
  const {api,pass,jobs}=fixture();
  const profile=api.profileFor(0,true,false,true);
  const warm=api.prewarm(profile);
  assert.equal(jobs[0].material.defines.FEATURE_WUYING_WOOD,1);
  jobs[0].resolve();
  await warm;
  pass.material.defines.FEATURE_WUYING_WOOD=1;
  assert.equal(api.kernelProgram(profile,false).cache,'prewarmed');
});

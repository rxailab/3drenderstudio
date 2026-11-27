import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {TraceDelivery, traceTileBatch, estimateTraceTileMs, fencePollMs} from '../static/viewport-trace-schedule.js';

test('publish first and final immediately, throttle whole intermediate samples', () => {
  const policy = new TraceDelivery(128);
  const sequence = [];
  for (let tile=1; tile<=128*3; tile++) {
    const sample = tile/3;
    if (!policy.due(sample, tile*20)) continue;
    sequence.push(sample);
    policy.submitted(sample, tile*20);
    policy.acknowledge(sample);
  }
  assert.deepEqual(sequence,[1,4,8,16,24,32,40,48,56,64,72,80,88,96,104,112,120,128]);
  const slow = new TraceDelivery(3);
  slow.submitted(1,0);
  assert.equal(slow.due(2,1000),false);
  slow.acknowledge(1);
  assert.equal(slow.due(2,1000),true);
  slow.submitted(2,1000);
  assert.equal(slow.due(3,1001),false);
  slow.acknowledge(1);
  assert.equal(slow.due(3,1001),false);
  slow.acknowledge(2);
  assert.equal(slow.due(3,1001),true);
  assert.equal(new TraceDelivery(1).due(1,0),true);
});

test('GPU bursts respect the cost estimate and end at the exact sample boundary', () => {
  assert.equal(traceTileBatch(0,48,20),1);
  assert.equal(traceTileBatch(0,48,2),5);
  assert.equal(traceTileBatch(0,48,.25),8);
  assert.equal(traceTileBatch(2 + 46/48,48,.25),2);
  assert.equal(traceTileBatch(1,1,.1),1);
});

async function workerFixture(tilesPerSample, limit, tileTime=0) {
  const worker = await readFile(new URL('../static/viewport-trace-worker.js',import.meta.url),'utf8');
  const tasks = new Map(), frames = [], batches = [], checks = [];
  let time=0, taskId=0, pendingTiles=0, inFlight=null;
  function enqueue(fn, delay) {
    tasks.set(++taskId,{fn,delay});
    return taskId;
  }
  class MessageChannel {
    port1 = {};
    port2 = {postMessage:data=>enqueue(()=>this.port1.onmessage({data}),.25)};
  }
  const driver = {
    TIMEOUT_EXPIRED:2, WAIT_FAILED:3, isContextLost:()=>false, flush(){},
    fenceSync(){
      assert.equal(inFlight,null,'only one GPU burst may be in flight');
      batches.push(pendingTiles);
      inFlight={done:time+pendingTiles*tileTime};
      pendingTiles=0;
      return inFlight;
    },
    clientWaitSync(sync){
      assert.equal(sync,inFlight);
      checks.push(time);
      return time<sync.done ? this.TIMEOUT_EXPIRED : 1;
    },
    deleteSync(sync){assert.equal(sync,inFlight); inFlight=null;},
  };
  const self = {addEventListener(){}, postMessage(frame){frames.push([frame.epoch,frame.samples]);}};
  const start = runInNewContext(worker.replace(/^import .*$/gm,'')+`
    gl=driver;
    canvas={transferToImageBitmap:()=>({})};
    renderer={};
    tracer={samples:0, target:{texture:{}}, _quad:{material:{uniforms:{traceSamples:{value:0}}},render(){}},
      renderSample(){recordTile(); this.samples+=1/tilesPerSample;}};
    ((epoch, ready=true) => {
      pause();
      const active=run={epoch,delivery:new TraceDelivery(limit),tileMs:10,tilesPerSample,ready,visible:true};
      tracer.samples=0;
      step();
      return ()=>{if (run===active) {active.ready=true;step();}};
    })
  `,{
    self,driver,TraceDelivery,traceTileBatch,estimateTraceTileMs,fencePollMs,MessageChannel,
    tilesPerSample,limit,recordTile:()=>pendingTiles++,THREE:{NoBlending:0},performance:{now:()=>time},
    setTimeout(fn,delay){return enqueue(fn,Math.max(16,delay));},
    clearTimeout(id){tasks.delete(id);},
  });
  const tick = () => {
    const [id,{fn,delay}]=tasks.entries().next().value;
    tasks.delete(id); time+=delay; fn();
  };
  const drain = () => {
    for (let n=0; tasks.size; n++) {
      assert.ok(n<500,'worker must finish even if the UI has not acknowledged a frame');
      tick();
    }
  };
  const message = data=>self.onmessage({data});
  return {start,tick,drain,message,frames,batches,checks};
}

test('a busy viewport receives the final accumulation on acknowledgement and a drag cancels stale work', async () => {
  const {start,tick,drain,message,frames,checks} = await workerFixture(2,5);
  start(1); drain();
  assert.deepEqual(frames,[[1,1]]);
  message({kind:'pause'});
  message({kind:'presented',epoch:1,samples:1});
  assert.deepEqual(frames,[[1,1]]);
  start(2); drain();
  message({kind:'presented',epoch:1,samples:1});
  assert.deepEqual(frames,[[1,1],[2,1]]);
  message({kind:'presented',epoch:2,samples:1});
  assert.deepEqual(frames,[[1,1],[2,1],[2,5]]);
  start(3); message({kind:'pause'}); start(4);
  const before=checks.length;
  tick(); // The first MessageChannel task belongs to the cancelled run.
  assert.equal(checks.length,before);
  message({kind:'pause'}); drain();
  assert.deepEqual(frames,[[1,1],[2,1],[2,5]]);
});

test('coarse timers allow fast batches to grow and a busy fence waits without spinning', async () => {
  const fast = await workerFixture(32,4,.6);
  fast.start(1);
  fast.drain();
  assert.ok(fast.batches.includes(8),'16 ms timer wakeups must not cap a fast GPU at one tile');
  assert.equal(fast.batches.reduce((a,b)=>a+b,0),128);
  const slow = await workerFixture(2,4,30);
  slow.start(2);
  slow.drain();
  assert.deepEqual(slow.batches,Array(8).fill(1));
  assert.equal(slow.checks[1]-slow.checks[0],16);
  assert.equal(slow.checks.length,3*slow.batches.length);
});

test('hiding keeps the current tile and delivers a pending final image on return', async () => {
  const {start,tick,drain,message,frames,batches,checks}=await workerFixture(2,8,30);
  const visible=value=>message({kind:'visibility',epoch:1,visible:value});
  start(1);
  tick(); // The driver is busy and a timed fence retry has been scheduled.
  visible(false);
  drain();
  assert.deepEqual(batches,[1]);
  assert.equal(checks.length,1);
  visible(true);
  drain();
  assert.equal(batches.reduce((a,b)=>a+b,0),16);
  assert.deepEqual(frames,[[1,1]]); // All 8 samples exist but the first bitmap still occupies the slot.
  visible(false);
  message({kind:'presented',epoch:1,samples:1});
  drain();
  assert.deepEqual(frames,[[1,1]]);
  visible(true);
  visible(true);
  drain();
  assert.deepEqual(frames,[[1,1],[1,8]]);
  assert.equal(batches.reduce((a,b)=>a+b,0),16);
});

test('compilation and stale visibility messages cannot start hidden or cancelled sampling', async () => {
  const {start,drain,message,frames,batches}=await workerFixture(1,2);
  const visibility=(epoch,visible)=>message({kind:'visibility',epoch,visible});
  const finishCompilation=start(1,false);
  visibility(1,false);
  visibility(1,true); // Shader still linking.
  visibility(1,false);
  finishCompilation();
  visibility(0,true);
  assert.deepEqual(batches,[]);
  visibility(1,true);
  visibility(1,false); // Posted fence check remains in the event queue.
  drain();
  assert.deepEqual(batches,[1]);
  assert.deepEqual(frames,[]);
  message({kind:'pause'});
  visibility(1,true);
  start(2);
  visibility(1,false);
  drain();
  assert.deepEqual(frames,[[2,1]]);
});

test('the tiled tracer blends once per complete sample and publishes the actual odd/even buffer', async () => {
  // Exercise the pinned generator with tiny CPU-backed render targets.
  const vendor = await readFile(new URL('../static/vendor/pathtracer/webgl-pathtracer.js',import.meta.url),'utf8');
  const begin = vendor.indexOf('function* renderTask()');
  const end = vendor.indexOf('var ogClearColor',begin);
  const render = runInNewContext(vendor.slice(begin,end)+'\nrenderTask', {Vector42:class {},NoBlending3:0});
  const getter = vendor.slice(end).match(/get target\(\) \{[\s\S]*?\n  \}/)[0];
  const targets = runInNewContext('({'+getter+'})');
  const target = () => ({texture:[0,0]});
  let tile, bound, blends=0;
  const renderer = {
    getRenderTarget:()=>bound, setRenderTarget:value=>bound=value,
    getScissorTest(){},getScissor(){},getViewport(){},setViewport(){},setScissor(){},setScissorTest(){},
  };
  const material = {
    resolution:{set(){}}, stratifiedTexture:{init(){},next(){}},seed:0,
  };
  const pass = Object.assign(Object.create(targets),{
    _renderer:renderer, _primaryTarget:{...target(),width:2,height:1,scissor:{set(x){tile=x;}},viewport:{set(){}}},
    _blendTargets:[target(),target()], _sobolTarget:{}, _subframe:[0,0,1,1],
    _opacityFactor:1, alpha:true,_alpha:true, samples:0, tiles:{x:2,y:1}, stableTiles:true,
    completeSamplesOnly:true,material,
    _fsQuad:{render(){bound.texture[tile]=material.seed+tile;}},
    _blendQuad:{material:{},render(){
      blends++;
      const {target1,target2,opacity}=this.material;
      for (let i=0;i<2;i++) bound.texture[i]=target1[i]*(1-opacity)+target2[i]*opacity;
    }},
  });
  const task = render.call(pass);
  for (let sample=1; sample<=128; sample++) {
    task.next();
    assert.equal(blends,sample-1);
    task.next();
    assert.equal(blends,sample);
    assert.equal(pass.samples,sample);
    for (let i=0;i<2;i++) assert.ok(Math.abs(pass.target.texture[i]-((sample+1)/2+i))<1e-10);
  }
});

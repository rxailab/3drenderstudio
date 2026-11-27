import test from 'node:test';
import assert from 'node:assert/strict';

// Isolate each simulated browser page and its module-level WebGL context cache.
let pages=0;
async function withPage(run) {
  const globals=['Worker','OffscreenCanvas','localStorage','window','document'];
  const original=globals.map(key=>Object.getOwnPropertyDescriptor(globalThis,key));
  const workers=[], tasks=new Map(), store=new Map(), events={};
  let id=0;
  class Worker {
    constructor(){this.messages=[];workers.push(this);}
    postMessage(message){this.messages.push(message);}
    terminate(){this.dead=true;}
    emit(data){this.onmessage({data});}
  }
  const page={hidden:false,addEventListener:(name,listener)=>events[name]=listener};
  const win={...page,requestIdleCallback:cb=>{tasks.set(++id,cb);return id;},cancelIdleCallback:i=>tasks.delete(i)};
  Object.assign(globalThis,{Worker,OffscreenCanvas:class{},document:page,window:win,
    localStorage:{getItem:k=>store.get(k)??null,setItem:(k,v)=>store.set(k,v)}});
  const tick=()=>{for(const [i,cb] of tasks){tasks.delete(i);cb();}};
  try {
    const cache=await import('../static/viewport-trace-cache.js?page='+ ++pages);
    await run({cache,workers,tick,tasks,page,events});
  } finally {
    globals.forEach((key,i)=>{if(original[i])Object.defineProperty(globalThis,key,original[i]);else delete globalThis[key];});
  }
}

test('visible idle startup warms once and a second project leases the same cleared worker',()=>withPage(async({cache,workers,tick})=>{
  cache.rememberTraceProfile({features:'scalar',camera:'ortho',background:true});
  cache.setTraceWarmupEnabled(true);
  cache.setTraceWarmupEnabled(true);
  assert.equal(workers.length,0);
  tick();
  assert.equal(workers.length,1);
  const worker=workers[0],ready={kind:'ready',adapterType:'hardware'};
  worker.emit(ready);
  assert.equal(worker.messages[1].kind,'prewarm');
  assert.equal(worker.messages[1].profile.camera,'ortho');
  worker.emit({kind:'warmed',done:true});
  const first=[],second=[];
  const lease=cache.acquireTraceWorker(data=>first.push(data),()=>assert.fail());
  await Promise.resolve();
  assert.deepEqual(first,[ready]);
  lease.release(true);
  assert.equal(worker.messages.at(-1).kind,'release');
  let closed=false;
  worker.emit({kind:'frame',bitmap:{close(){closed=true;}}});
  assert.equal(closed,true);
  const next=cache.acquireTraceWorker(data=>second.push(data),()=>assert.fail());
  assert.equal(next.worker,worker);
  await Promise.resolve();
  assert.deepEqual(second,[ready]);
  assert.equal(first.length,1);
  next.release(true);
  cache.setTraceWarmupEnabled(false);
  assert.equal(worker.dead,true);
}));

test('opening a model before init completes takes priority without starting a second worker',()=>withPage(async({cache,workers,tick})=>{
  cache.setTraceWarmupEnabled(true);
  tick();
  const worker=workers[0], seen=[];
  const lease=cache.acquireTraceWorker(data=>seen.push(data.kind),()=>assert.fail());
  worker.emit({kind:'ready'});
  assert.deepEqual(seen,['ready']);
  assert.deepEqual(worker.messages.map(x=>x.kind),['init','claim']);
  lease.release(false);
  assert.equal(worker.dead,true);
}));

test('hidden or disabled pages do no speculative work and a failed warmup allows on-demand retry',()=>withPage(async({cache,workers,tick,page,events})=>{
  page.hidden=true;
  cache.setTraceWarmupEnabled(true);
  tick();assert.equal(workers.length,0);
  page.hidden=false;events.visibilitychange();
  tick();assert.equal(workers.length,1);
  workers[0].emit({kind:'unavailable',reason:'context lost'});
  assert.equal(workers[0].dead,true);
  const lease=cache.acquireTraceWorker(()=>{},()=>assert.fail());
  assert.equal(workers.length,2);
  lease.release(false);
  cache.setTraceWarmupEnabled(false);
  events.visibilitychange();
  tick();assert.equal(workers.length,2);
}));

test('orbit during shader compilation keeps the context and ignores stale first frames across projects',()=>withPage(async({workers,tick})=>{
  const cache=await import('../static/viewport-trace-cache.js');
  const {ViewportPathTracing}=await import('../static/viewport-path-tracing.js');
  const view=()=>({visible:true,frame:{dataset:{}},requestDraw(){}});
  cache.setTraceWarmupEnabled(true);tick();
  const worker=workers[0], first=new ViewportPathTracing(view());
  const connecting=first.openWorker();
  worker.emit({kind:'ready',adapterType:'hardware',maxTextureSize:16384});
  await connecting;
  const old=first.buildEpoch=first.epoch;
  first.running=first.building=true;
  worker.emit({kind:'stage',stage:'compile',epoch:old});
  first.view.orbiting=true;
  for(let i=0;i<12;i++)first.reset();
  assert.equal(first.worker,worker);
  assert.equal(worker.messages.filter(m=>m.kind==='pause').length,1);
  worker.emit({kind:'sceneReady',epoch:old});
  assert.equal(first.building,false);
  first.view.orbiting=false;
  first.running=true;
  let stale=0;
  const staleFrame={kind:'frame',epoch:old,samples:1,limit:128,bitmap:{close(){stale++;}}};
  worker.emit(staleFrame);
  assert.equal(stale,1);
  assert.equal(first.running,true);
  assert.equal(await first.openWorker(),true);
  first.dispose();
  const second=new ViewportPathTracing(view());
  await second.openWorker();
  assert.equal(second.worker,worker);
  second.running=true;
  worker.emit(staleFrame);
  assert.equal(stale,2);
  assert.equal(second.running,true);
  assert.equal(second.hasFrame,undefined);
  second.dispose();
  cache.setTraceWarmupEnabled(false);
}));

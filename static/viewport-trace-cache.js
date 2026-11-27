// A linked WebGL program belongs to its context. Keep one unleased worker
// in this page, and return it only after asking it to release the old scene.
const profileKey='wuying-trace-kernel-profile-v1';
let idle, leases=0, scheduled, enabled=false, watching=false;

function cancelScheduled() {
  if(scheduled===undefined)return;
  if(window.cancelIdleCallback)window.cancelIdleCallback(scheduled);
  else clearTimeout(scheduled);
  scheduled=undefined;
}

function startWorker() {
  const worker=new Worker(new URL('./viewport-trace-worker.js',import.meta.url),{type:'module',name:'物影路径追踪'});
  const entry={worker};
  worker.onmessage=({data})=>{
    if(data.kind==='ready')entry.info=data;
    if(entry.receive)entry.receive(data);
    else if(entry===idle) {
      if(data.kind==='unavailable'){discardIdle();return;}
      if(data.kind==='ready' && entry.prewarm) {
        let profile;
        try {profile=JSON.parse(localStorage.getItem(profileKey));} catch {}
        worker.postMessage({kind:'prewarm',profile});
      }
      if(data.kind==='warmIdle' || (data.kind==='warmed' && data.done))entry.prewarm=false;
      data.bitmap?.close();
    } else data.bitmap?.close();
  };
  worker.onerror=event=>{
    if(entry.error)entry.error(event);
    else if(entry===idle)discardIdle();
  };
  worker.postMessage({kind:'init'});
  return entry;
}

function discardIdle() {
  idle?.worker.terminate();
  idle=undefined;
}

export function interruptTraceWarmup() {
  cancelScheduled();
  if(idle?.prewarm) {
    idle.prewarm=false;
    idle.worker.postMessage({kind:'claim'});
  }
}

function schedule() {
  if(!enabled || document.hidden || leases || idle || scheduled!==undefined ||
      typeof Worker==='undefined' || typeof OffscreenCanvas==='undefined')return;
  const warm=()=>{
    scheduled=undefined;
    if(!enabled || document.hidden || leases || idle)return;
    // A failed speculative context must leave normal on-demand startup usable.
    try {idle=startWorker();idle.prewarm=true;} catch {discardIdle();}
  };
  scheduled=window.requestIdleCallback?window.requestIdleCallback(warm,{timeout:300}):setTimeout(warm,0);
}

export function setTraceWarmupEnabled(value) {
  enabled=Boolean(value);
  if(!watching) {
    watching=true;
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden){
        cancelScheduled();
        if(idle?.prewarm)discardIdle();
      } else schedule();
    });
    window.addEventListener('pagehide',()=>{cancelScheduled();discardIdle();});
    window.addEventListener('pageshow',schedule);
  }
  if(enabled)schedule();
  else {cancelScheduled();discardIdle();}
}

export function acquireTraceWorker(receive,error) {
  cancelScheduled();
  const cached=idle;
  const entry=cached || startWorker();
  idle=undefined;
  leases++;
  entry.receive=receive;
  entry.error=error;
  if(cached)entry.worker.postMessage({kind:'claim'});
  entry.prewarm=false;
  // Resolve through the same handler as a cold connection. Install the
  // lease first, and ignore an acquisition cancelled in this microtask.
  if(entry.info)queueMicrotask(()=>{if(entry.receive===receive)receive(entry.info);});
  return {worker:entry.worker,release(reuse){
    if(entry.receive!==receive)return;
    leases--;
    entry.receive=entry.error=undefined;
    if(reuse && enabled && !idle) {
      entry.worker.postMessage({kind:'release'});
      idle=entry;
    } else entry.worker.terminate();
  }};
}

export function rememberTraceProfile(profile) {
  if(!profile)return;
  try {
    const value=JSON.stringify(profile);
    if(localStorage.getItem(profileKey)!==value)localStorage.setItem(profileKey,value);
  } catch {}
}

import * as THREE from './vendor/three/build/three.module.js';
import { serializeTraceScene,traceTextureLayout } from './viewport-trace-scene.js';
import { acquireTraceWorker, interruptTraceWarmup, rememberTraceProfile } from './viewport-trace-cache.js';

// Replies already in the message queue can outlive a project change.
let lastEpoch=0;

const preparationText={
  device:'正在连接后台图形设备',
  copy:'正在复制模型与材质',
  bvh:'正在构建光线加速结构',
  lighting:'正在更新摄影棚布光',
  compile:'显卡正在编译光追着色器',
  first_sample:'正在计算第一帧光追',
};

function measureScene(model) {
  let triangles = 0, vertices = 0, sourceBytes = 0;
  const buffers = new Set(), materials = new Set();
  model.traverseVisible(node=>{
    const g = node.isMesh && node.geometry;
    if (!g?.attributes.position) return;
    const instances = node.isInstancedMesh ? node.count : 1;
    triangles += (g.index?.count ?? g.attributes.position.count)*instances/3;
    vertices += g.attributes.position.count*instances;
    for (const attr of [g.index,...Object.values(g.attributes)]) {
      const array = attr?.isInterleavedBufferAttribute ? attr.data.array : attr?.array;
      if (array && !buffers.has(array.buffer)) { buffers.add(array.buffer); sourceBytes+=array.byteLength; }
    }
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      materials.add(material);
    }
  });
  // Peak estimate covers the isolated source, flattening/BVH build buffers
  // and packed GPU vertex/triangle data. The texture atlas has its own cap.
  const materialLayout=traceTextureLayout(materials);
  const bytes = sourceBytes*2 + vertices*112 + triangles*78 + 4*materialLayout.width*materialLayout.height*materialLayout.layers + 64*1024**2;
  return {triangles,vertices,bytes,materialLayout};
}

export class ViewportPathTracing {
  constructor(view, onState) {
    this.view = view;
    this.onState = onState;
    this.enabled = true;
    this.epoch = ++lastEpoch;
    this.revision = 0;
    this.sentRevision = -1;
    this.studioRevision = 0;
    this.sentStudioRevision = -1;
    this.adapterType = 'unknown';
    this.adapterName = '';
    this.setState('raster','interactive');
  }

  get limit() { return this.requestedLimit || 128; }

  setState(mode, phase, samples = 0, reason = '', stage = '') {
    const image=mode==='pathtracing' ? this.texture?.image : null;
    const state = {mode,phase,samples,limit:this.limit,reason,stage,adapterType:this.adapterType,adapterName:this.adapterName,
      width:image?.width || 0,height:image?.height || 0};
    if (this.state && Object.keys(state).every(key=>state[key] === this.state[key])) return;
    this.state = state;
    Object.assign(this.view.frame.dataset,{traceMode:mode,tracePhase:phase,traceStage:stage,traceSamples:String(samples),traceLimit:String(state.limit)});
    if (!this.view.disposed) this.onState?.(state);
  }

  preparing(stage) { this.setState('raster','preparing',0,preparationText[stage],stage); }

  options({enabled=this.enabled, limit=this.requestedLimit} = {}) {
    const value = Number(limit);
    const next = Number.isFinite(value) && value > 0 ? Math.max(1,Math.min(256,Math.round(value))) : undefined;
    if (enabled === this.enabled && next === this.requestedLimit) return;
    this.enabled = !!enabled;
    this.requestedLimit = next;
    if (!this.enabled) { this.releaseWorker(); this.releaseImage(); }
    this.reason = '';
    this.view.requestDraw();
  }

  reset() {
    this.epoch=++lastEpoch;
    if(this.view.orbiting || this.view.previewTarget)interruptTraceWarmup();
    this.abort?.abort();
    this.abort = null;
    if (this.running) this.worker?.postMessage({kind:'pause'});
    this.running = false;
    this.hasFrame = false;
    if (!this.reason) this.setState('raster','interactive');
  }

  setVisible(visible) {
    if (!visible) {
      // A scene copy that has not reached the worker can be retried on return.
      this.abort?.abort();
      this.abort = null;
    }
    if (this.running) this.worker?.postMessage({kind:'visibility',epoch:this.epoch,visible});
  }

  invalidate() {
    this.revision++;
    // A stale long BVH build can be discarded wholesale. A warm context
    // otherwise keeps its linked ray-tracing shader across material edits.
    if (this.building) this.releaseWorker();
    this.reason = '';
    this.reset();
  }

  invalidateBackdrop() {
    this.studioRevision++;
    this.reset();
  }

  releaseWorker(reuse=false) {
    this.lease?.release(reuse);
    this.lease=null;
    this.worker = null;
    this.resolveReady?.(false);
    this.resolveReady = null;
    this.ready = null;
    this.running = false;
    this.building = false;
    this.sentRevision = -1;
    this.sentStudioRevision = -1;
    this.kernelProfile=null;
  }

  fallback(reason) {
    this.releaseWorker();
    this.releaseImage();
    this.reason = reason || '图形设备不支持路径追踪';
    this.hasFrame = false;
    this.setState('fallback','unavailable',0,this.reason);
    if (!this.view.disposed) this.view.requestDraw();
  }

  openWorker() {
    if (this.ready) return this.ready;
    if (typeof Worker === 'undefined' || typeof OffscreenCanvas === 'undefined') throw new Error('浏览器不支持后台图形计算');
    this.ready = new Promise(resolve=>{this.resolveReady=resolve;});
    this.lease=acquireTraceWorker(data=>{
      if (worker !== this.worker) { data.bitmap?.close(); return; }
      if (data.kind === 'ready') {
        this.adapterType = data.adapterType;
        this.adapterName = data.adapterName;
        this.maxTextureSize = data.maxTextureSize;
        this.resolveReady?.(true);
        this.resolveReady = null;
      } else if (data.kind === 'unavailable') {
        this.adapterType = data.adapterType;
        this.adapterName = data.adapterName;
        this.fallback(data.reason);
      } else if (data.kind === 'sceneReady' && data.epoch===this.buildEpoch) {
        this.building = false;
      } else if(data.kind==='kernel' && data.epoch===this.epoch) {
        this.kernelProfile=data.profile;
        this.profileEpoch=data.epoch;
      } else if (data.kind === 'stage' && data.epoch===this.epoch && this.running) {
        this.preparing(data.stage);
      } else if (data.kind === 'frame') {
        try {
          if (this.epoch !== data.epoch || !this.running || this.view.disposed) { data.bitmap.close(); return; }
          this.showFrame(data.bitmap,data.samples);
          if(this.profileEpoch===data.epoch && this.kernelProfile) {
            rememberTraceProfile(this.kernelProfile);
            this.kernelProfile=null;
          }
          this.running = data.samples < data.limit;
          this.setState('pathtracing',this.running ? 'sampling' : 'complete',data.samples);
        } catch (error) { this.fallback(error.message); }
        finally {
          if (worker === this.worker) worker.postMessage({kind:'presented',epoch:data.epoch,samples:data.samples});
        }
      }
    },event=>{if(this.worker===worker)this.fallback(event.message || '路径追踪模块加载失败');});
    const worker=this.worker=this.lease.worker;
    return this.ready;
  }

  async start() {
    const v = this.view;
    if (!this.enabled || this.reason || this.running || this.abort || (this.hasFrame && this.imageSamples >= this.limit) || !v.model || v.disposed || !v.visible || document.hidden || v.orbiting || v.previewTarget) return;
    const epoch = this.epoch, abort = new AbortController();
    this.abort = abort;
    this.preparing(!this.ready ? 'device' : this.sentRevision !== this.revision ? 'copy' : 'first_sample');
    try {
      if (!await this.openWorker() || abort.signal.aborted) return;
      let snapshot;
      let measure;
      const rebuild=this.sentRevision!==this.revision;
      const studioChanged=this.sentStudioRevision!==this.studioRevision;
      if (rebuild) {
        this.preparing('copy');
        measure = measureScene(v.model);
        if (Math.max(measure.vertices,measure.triangles*2) > this.maxTextureSize**2) throw new Error('模型的加速结构超过显卡单张纹理的尺寸上限');
        const ram = navigator.deviceMemory || 8;
        const gib = Math.min(3.5,ram*(ram<=4 ? .27 : .43));
        if (measure.bytes > gib*1024**3) {
          throw new Error('此模型预计占用 '+(measure.bytes/1024**3).toFixed(1)+' GB，超过当前设备的 '+gib.toFixed(1)+' GB 光追内存预算');
        }
      }
      if(rebuild || studioChanged) {
        if(!rebuild)this.preparing('lighting');
        // Keep the worker's current BVH, model textures and geometry when
        // only the photographic set changes.
        snapshot = await serializeTraceScene(rebuild?v.model:new THREE.Group(),v.studioMetadata || v.metadata,v.bounds,{
          signal:abort.signal,environment:v.studioRadiance || v.scene.environment,backplate:v.plate,materialLayout:measure?.materialLayout});
        if (abort.signal.aborted) {
          for (const item of snapshot.transfer) item.close?.();
          return;
        }
      }
      v.camera.updateMatrixWorld();
      const {width,height} = v.renderer.domElement;
      const camera = {orthographic:v.camera.isOrthographicCamera,world:v.camera.matrixWorld.toArray(),projection:v.camera.projectionMatrix.toArray()};
      this.running = true;
      if (rebuild) {this.building = true;this.buildEpoch=epoch;}
      this.preparing(rebuild ? 'bvh' : 'first_sample');
      this.worker.postMessage({kind:'run',epoch,camera,width,height,limit:this.limit,exposure:v.renderer.toneMappingExposure,
        payload:rebuild?snapshot.payload:undefined,studio:rebuild?undefined:snapshot?.payload,triangles:measure?.triangles},snapshot?.transfer || []);
      this.sentRevision = this.revision;
      this.sentStudioRevision=this.studioRevision;
    } catch (error) {
      if (!abort.signal.aborted) this.fallback(error.message);
    } finally {
      if (this.abort === abort) this.abort = null;
    }
  }

  showFrame(bitmap,samples) {
    const previous = this.texture?.image;
    if (previous?.width !== bitmap.width || previous?.height !== bitmap.height) {
      this.texture?.dispose();
      this.texture = new THREE.Texture();
      this.texture.generateMipmaps = false;
      this.texture.minFilter = this.texture.magFilter = THREE.LinearFilter;
    }
    this.texture.image = bitmap;
    this.texture.needsUpdate = true;
    this.hasFrame = true;
    this.imageSamples = samples;
    // A bitmap may already be in flight when switching to the rendered image.
    // Retain it (including the final sample) without drawing to a hidden canvas.
    try { if(this.view.visible && !document.hidden)this.present(); } finally { previous?.close(); }
  }

  present() { this.view.refinement.presentTrace(this.texture,this.imageSamples,this.limit); }

  releaseImage() {
    this.texture?.image?.close();
    this.texture?.dispose();
    this.texture = null;
    this.hasFrame = false;
  }

  dispose() {
    this.reset();
    this.releaseWorker(!this.building);
    this.releaseImage();
  }
}

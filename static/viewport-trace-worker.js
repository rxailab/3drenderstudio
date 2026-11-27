import * as THREE from './vendor/three/build/three.module.js';
import { WebGLPathTracer } from './vendor/pathtracer/webgl-pathtracer.js';
import { buildTraceScene, configureTraceBackdrop, alignTraceBackdrop } from './viewport-trace-scene.js';
import { traceKernelFeatures, kernelForFeatures, validKernelFeatures } from './viewport-trace-kernel.js';
import { traceDevice } from './viewport-trace-device.js';
import { TraceDelivery, traceTileBatch, estimateTraceTileMs, fencePollMs } from './viewport-trace-schedule.js';
import { configureTraceOutput } from './viewport-trace-display.js';

let canvas, renderer, tracer, gl, sceneHandle, camera, run, timer, fence;
let fenceChecks, fenceTicket = 0;
let adapterType = 'unknown', adapterName = '';
let fullKernel, sceneFeatures, activeKey, sharedPasses, warmGeneration = 0, warmTimer;
// Each entry pins only a linked WebGL program. Geometry, textures and the
// application's material uniforms belong exclusively to the current scene.
const programs = new Map();

function stage(name) {
  if (!run || run.stage === name) return;
  run.stage = name;
  self.postMessage({kind:'stage', epoch:run.epoch, stage:name});
}

function pause() {
  clearTimeout(timer);
  fenceTicket++;
  run = null;
  if (fence) gl.deleteSync(fence);
  fence = null;
}

function checkFenceSoon() {
  if (!fenceChecks) {
    fenceChecks = new MessageChannel();
    fenceChecks.port1.onmessage = ({data}) => { if (data === fenceTicket) step(); };
  }
  // Yield to worker messages without incurring a coarse Windows timer tick.
  // Only the first check uses the port. A busy GPU gets a delayed retry.
  fenceChecks.port2.postMessage(++fenceTicket);
}

function fail(message) {
  pause();
  self.postMessage({kind:'unavailable', reason:message, adapterType, adapterName});
  self.close();
}

function initialize() {
  canvas = new OffscreenCanvas(1,1);
  // transferToImageBitmap preserves the canvas' premultiplied representation.
  // Let the path tracer's output quad write that representation explicitly.
  renderer = new THREE.WebGLRenderer({canvas, alpha:true, premultipliedAlpha:true, antialias:false, powerPreference:'high-performance'});
  renderer.setClearColor(0,0);
  gl = renderer.getContext();
  const info = gl.getExtension('WEBGL_debug_renderer_info');
  const name = (info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : '') || '';
  const vendor = (info ? gl.getParameter(info.UNMASKED_VENDOR_WEBGL) : '') || '';
  ({adapterName,adapterType}=traceDevice(name,vendor));
  if (adapterType !== 'hardware') {
    throw new Error('光追需要 NVIDIA 显卡，当前设备为 '+adapterName+'。请在系统图形设置中为浏览器选择 NVIDIA 高性能显卡，然后重新启动浏览器。');
  }
  if (!gl.getExtension('EXT_color_buffer_float') || gl.getParameter(gl.MAX_TEXTURE_IMAGE_UNITS) < 16) {
    throw new Error('此设备缺少路径追踪需要的浮点缓冲或纹理单元');
  }
  renderer.debug.onShaderError = () => { throw new Error('此图形驱动未能编译路径追踪着色器'); };
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  canvas.addEventListener('webglcontextlost', () => fail('路径追踪的图形连接已中断'));
  self.postMessage({kind:'ready', adapterType, adapterName, maxTextureSize:gl.getParameter(gl.MAX_TEXTURE_SIZE)});
}

function readCamera(data) {
  const result = data.orthographic ? new THREE.OrthographicCamera() : new THREE.PerspectiveCamera();
  result.matrixAutoUpdate = false;
  result.matrix.fromArray(data.world);
  result.updateMatrixWorld(true);
  result.projectionMatrix.fromArray(data.projection);
  result.projectionMatrixInverse.copy(result.projectionMatrix).invert();
  return result;
}

function prepareTracer() {
  if (tracer) return;
  tracer = new WebGLPathTracer(renderer);
  // The library normally compiles on each intermediate define change.
  // Link only complete variants, with the exact linear render target.
  for (const pass of [tracer._pathTracer,tracer._lowResPathTracer]) {
    pass.material.removeEventListener('recompilation',pass._compileFunction);
  }
  configureTraceBackdrop(tracer);
  configureTraceOutput(tracer._quad.material);
  fullKernel = tracer._pathTracer.material.fragmentShader;
  tracer.renderDelay = 0;
  tracer.fadeDuration = 0;
  tracer.minSamples = 1;
  tracer.rasterizeScene = false;
  tracer.bounces = 4;
  tracer.transmissiveBounces = 8;
  tracer.filterGlossyFactor = 0.15;
  tracer.renderToCanvas = false;
  tracer._pathTracer.completeSamplesOnly = true;
}

function profileFor(features, orthographic, background,wood=false) {
  return {version:1,features,camera:orthographic?'orthographic':'perspective',background,wood};
}

function validProfile(p) {
  return p?.version===1 && validKernelFeatures(p.features) &&
    (p.camera==='orthographic' || p.camera==='perspective') && typeof p.background==='boolean' &&
    (p.wood===undefined || typeof p.wood==='boolean');
}

function stopWarmup() {
  warmGeneration++;
  clearTimeout(warmTimer);
}

function withRenderTarget(target,fn) {
  const previous=renderer.getRenderTarget();
  renderer.setRenderTarget(target);
  try { return fn(); } finally { renderer.setRenderTarget(previous); }
}

function warmSharedPasses() {
  if (sharedPasses) return sharedPasses;
  const pass=tracer._pathTracer, output=tracer._quad;
  const view=camera || new THREE.PerspectiveCamera();
  const blend=withRenderTarget(pass._blendTargets[1],()=>renderer.compileAsync(pass._blendQuad._mesh,view));
  // Map presence and the sRGB canvas change Three's program key. Match the
  // real present() bindings even while the accumulation targets are 1x1.
  const previous=output.material.map;
  output.material.map=pass._blendTargets[1].texture;
  let display;
  try {
    display=withRenderTarget(null,()=>renderer.compileAsync(output._mesh,view));
  } catch(error) {
    display=Promise.reject(error);
  } finally { output.material.map=previous; }
  sharedPasses=Promise.all([blend,display]).catch(error=>{sharedPasses=null;throw error;});
  return sharedPasses;
}

function kernelProgram(profile, backgroundTask) {
  const pass=tracer._pathTracer, material=pass.material;
  const defines={...material.defines};
  if (backgroundTask) {
    // These are the values PhysicalPathTracingMaterial.onBeforeRender() and
    // setCamera() choose for a regular product camera with no volumetric fog.
    defines.CAMERA_TYPE=profile.camera==='orthographic'?1:0;
    defines.FEATURE_BACKGROUND_MAP=Number(profile.background);
    defines.FEATURE_WUYING_WOOD=Number(!!profile.wood);
    defines.FEATURE_DOF=0;
    defines.FEATURE_FOG=0;
  }
  const key=profile.features+'/'+JSON.stringify(defines);
  let entry=programs.get(key);
  if (entry) {
    programs.delete(key);
    programs.set(key,entry);
    return {entry,cache:!entry.ready?'pending':entry.warm?'prewarmed':'reused'};
  }
  const fragment=kernelForFeatures(fullKernel,profile.features);
  // Use a separate material to own this program while the real renderer
  // switches models. It deliberately carries no BVH, uniforms or textures.
  const pin=new THREE.ShaderMaterial({vertexShader:material.vertexShader,fragmentShader:fragment,
    defines,transparent:material.transparent,side:material.side,depthWrite:material.depthWrite,
    blending:THREE.NoBlending,toneMapped:false});
  const mesh=new THREE.Mesh(pass._fsQuad._mesh.geometry,pin);
  entry={key,pin,fragment,ready:false,warm:backgroundTask};
  programs.set(key,entry);
  if (programs.size>4) {
    const [oldest,old]=programs.entries().next().value;
    programs.delete(oldest);
    old.pin.dispose();
  }
  try {
    const pending=withRenderTarget(pass._primaryTarget,()=>renderer.compileAsync(mesh,camera || new THREE.PerspectiveCamera()));
    entry.promise=pending.then(()=>{
      if (programs.get(key)!==entry) return;
      const program=renderer.properties.get(pin).currentProgram;
      if (!program || !gl.getProgramParameter(program.program,gl.LINK_STATUS)) throw new Error('显卡未能链接光追着色器');
      entry.ready=true;
    }).catch(error=>{
      if (programs.get(key)===entry) programs.delete(key);
      pin.dispose();
      throw error;
    });
  } catch(error) {
    programs.delete(key);
    pin.dispose();
    throw error;
  }
  return {entry,cache:'miss'};
}

function prewarm(previous) {
  stopWarmup();
  const generation=warmGeneration;
  const idle = status=>{
    if (generation===warmGeneration) self.postMessage({kind:'warmIdle',status});
  };
  if (sceneHandle || !gl.getExtension('KHR_parallel_shader_compile')) { idle('skipped'); return; }
  async function warm(profile,done) {
    try {
      prepareTracer();
      const {entry}=kernelProgram(profile,true);
      await Promise.all([entry.promise,warmSharedPasses()]);
      if (generation!==warmGeneration) return false;
      self.postMessage({kind:'warmed',profile,done});
      if (done) idle('complete');
      return true;
    } catch {
      idle('failed');
      return false;
    }
  }
  const optical=traceKernelFeatures([{transmission:1,roughness:.008},{metalness:1,roughness:.025}]);
  const first=validProfile(previous)?previous:profileFor(optical,true,false);
  const second=profileFor(first.features===optical?0:optical,true,false);
  return warm(first,false).then(ok=>{
    if (ok && generation===warmGeneration) {
      // Give navigation a chance to claim the worker between compiler jobs.
      warmTimer=setTimeout(()=>warm(second,true),450);
    }
  });
}

function releaseScene() {
  stopWarmup();
  pause();
  if (!sceneHandle) return;
  sceneHandle.dispose();
  sceneHandle=null;
  activeKey=null;
  const pass=tracer._pathTracer, material=pass.material;
  material.dispose();
  // Replacing the generator's contents drops the old merged mesh/BVH and
  // shrinks its attribute textures and atlas. Clear the last HDR separately:
  // upstream's updateEnvironment() keeps that texture when given a null sky.
  tracer.textureSize.set(1,1);
  tracer.setScene(new THREE.Scene(),new THREE.PerspectiveCamera());
  material.envMapInfo.dispose();
  material.envMapInfo=new material.envMapInfo.constructor();
  pass.setSize(1,1);
  tracer._lowResPathTracer.setSize(1,1);
  tracer._quad.material.map=null;
  renderer.setSize(1,1,false);
  camera=null;
}

function begin(message) {
  stopWarmup();
  pause();
  run = {epoch:message.epoch, delivery:new TraceDelivery(message.limit), tileMs:10};
  camera = readCamera(message.camera);
  prepareTracer();
  if (message.payload) {
    stage('bvh');
    sceneHandle?.dispose();
    sceneHandle = buildTraceScene(message.payload);
    tracer._generator.attributes=['position','normal','tangent','color',...sceneHandle.uvAttributes];
    const atlas=sceneHandle.materialLayout;
    if (atlas.layers > gl.getParameter(gl.MAX_ARRAY_TEXTURE_LAYERS)) throw new Error('此场景的贴图数量超过显卡限制');
    tracer.textureSize.set(atlas.width,atlas.height);
    // For large CAD meshes, center splits with small groups of triangles cut
    // the BVH build time and texture footprint. Intersections still use the
    // original triangles, including thin shells and glass.
    tracer._generator.bvhOptions = message.triangles > 350000 ? {strategy:0,maxLeafTris:6} : {};
    const {materials} = tracer.setScene(sceneHandle.scene,camera);
    sceneFeatures = traceKernelFeatures(materials);
    self.postMessage({kind:'sceneReady',epoch:message.epoch});
  } else {
    if(message.studio){
      stage('lighting');
      sceneHandle.updateStudio(message.studio);
      tracer.updateEnvironment();tracer.updateLights();tracer.updateMaterials();
    }
    tracer.setCamera(camera);
  }
  alignTraceBackdrop(tracer._pathTracer.material,camera,sceneHandle);
  renderer.toneMappingExposure = message.exposure;
  // These are the settled canvas's physical backing dimensions. Its pixel
  // budget is already applied; another resize here blurs the finished image.
  const {width,height}=message;
  renderer.setSize(width,height,false);
  const edge = 144;
  tracer.tiles.set(Math.ceil(width/edge),Math.ceil(height/edge));
  run.tilesPerSample = tracer.tiles.x * tracer.tiles.y;
  const pass=tracer._pathTracer, active=run;
  // The photographic backdrop has fractional coverage. Start with the
  // correct accumulation mode instead of rendering and discarding a tile.
  pass.alpha = true;
  pass.material.blending = THREE.NoBlending;
  pass.material.toneMapped = false;
  pass.material.onBeforeRender();
  const profile=profileFor(sceneFeatures,Boolean(camera.isOrthographicCamera),Boolean(sceneHandle.scene.background),Boolean(pass.material.defines.FEATURE_WUYING_WOOD));
  const {entry,cache}=kernelProgram(profile,false);
  if (activeKey!==entry.key) {
    // Pins above own the bounded cache. Do not let the active material retain
    // every variant it has ever used as a second unbounded program cache.
    pass.material.dispose();
    pass.material.fragmentShader=entry.fragment;
    pass.material.needsUpdate=true;
    activeKey=entry.key;
  }
  if (!entry.ready) stage('compile');
  entry.promise.then(()=>{
    if (run!==active) return;
    withRenderTarget(pass._primaryTarget,()=>renderer.compile(pass._fsQuad._mesh,camera));
    const actual=renderer.properties.get(pass.material).currentProgram;
    const exact=actual && actual===renderer.properties.get(entry.pin).currentProgram;
    self.postMessage({kind:'kernel',epoch:active.epoch,profile,cache:exact?cache:'miss'});
    entry.warm=false;
    stage('first_sample');
    step();
  }).catch(error=>{if (run===active) fail(error.message);});
}

function present(samples, now) {
  const {delivery, epoch} = run;
  if (!delivery.due(samples, now)) return;
  const quad = tracer._quad;
  quad.material.uniforms.traceSamples.value=samples;
  quad.material.opacity = 1;
  quad.material.map = tracer.target.texture;
  quad.material.blending = THREE.NoBlending;
  quad.render(renderer);
  const bitmap = canvas.transferToImageBitmap();
  self.postMessage({kind:'frame', epoch, samples, limit:delivery.limit, bitmap},[bitmap]);
  delivery.submitted(samples,now);
}

function step() {
  if (!run) return;
  try {
    if (gl.isContextLost()) throw new Error('路径追踪的图形连接已中断');
    // A short bounded batch can finish after a drag starts. All remaining
    // work stays in JS and is discarded immediately by pause().
    if (fence) {
      const status = gl.clientWaitSync(fence,0,0);
      if (status === gl.TIMEOUT_EXPIRED) {
        run.batchBusyMs = performance.now() - run.batchStarted;
        timer = setTimeout(step, fencePollMs);
        return;
      }
      if (status === gl.WAIT_FAILED) throw new Error('显卡未完成路径追踪计算');
      gl.deleteSync(fence);
      fence = null;
      const now = performance.now();
      run.tileMs = estimateTraceTileMs(run.tileMs, run.batchTiles, run.batchBusyMs, now - run.batchStarted);
      if (run.stage==='compile') stage('first_sample');
      present(tracer.samples, now);
    }
    if (tracer.samples >= run.delivery.limit) {
      // If the UI is busy, retain the final accumulation on the GPU. Its
      // acknowledgement releases the slot and triggers the final transfer.
      present(tracer.samples, performance.now());
      if (run.delivery.sent === run.delivery.limit) pause();
      return;
    }
    run.batchStarted = performance.now();
    run.batchTiles = traceTileBatch(tracer.samples, run.tilesPerSample, run.tileMs);
    for (let i=0; i<run.batchTiles; i++) {
      tracer.renderSample();
      if (tracer.isCompiling) { timer = setTimeout(step,16); return; }
    }
    fence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE,0);
    if (!fence) throw new Error('无法同步显卡光追任务');
    gl.flush();
    run.batchBusyMs = performance.now() - run.batchStarted;
    checkFenceSoon();
  } catch (error) { fail(error.message); }
}

self.addEventListener('unhandledrejection', event => { event.preventDefault(); fail(event.reason?.message || '显卡编译失败'); });
self.onmessage = ({data}) => {
  try {
    if (data.kind === 'init') initialize();
    if (data.kind === 'prewarm') prewarm(data.profile);
    if (data.kind === 'claim') stopWarmup();
    if (data.kind === 'release') releaseScene();
    if (data.kind === 'run') begin(data);
    if (data.kind === 'pause') pause();
    if (data.kind === 'presented' && run?.epoch === data.epoch) {
      run.delivery.acknowledge(data.samples);
      if (tracer.samples >= run.delivery.limit && !fence) step();
    }
  } catch (error) { fail(error.message); }
};

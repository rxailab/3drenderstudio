import * as THREE from 'three';
import { OrbitControls } from './vendor/three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from './vendor/three/examples/jsm/loaders/GLTFLoader.js';
import { cameraFromShot, shotFromCamera, aspectNumber, validateBounds } from './viewport-math.mjs';
import { makeLibraryPreviewMaterial } from './viewport-library-material.js';
import { assignGeneratedSpaces } from './viewport-wood-material.js';
import { SelectionMaterials } from './viewport-selection.js';
import { ViewportRefinement } from './viewport-refinement.js';
import { buildStudioLighting,studioEnvironmentMap } from './viewport-studio.js';
import { ViewportPathTracing } from './viewport-path-tracing.js';
import { setTraceWarmupEnabled } from './viewport-trace-cache.js';
import { FastViewportOptics } from './viewport-fast-optics.js';
import { backdropPreset,loadBackdrop,fitBackdrop,backdropLighting,backdropRadiance,loadStudioEnvironment,prepareBackdropLights } from './viewport-backdrop.js';

function disposeTree(root) {
  const textures=new Set(),materials=new Set(),geometries=new Set();
  root?.traverse(node=>{
    if(node.geometry)geometries.add(node.geometry);
    for(const material of (Array.isArray(node.material)?node.material:[node.material]).filter(Boolean)) {
      materials.add(material);for(const value of Object.values(material))if(value?.isTexture)textures.add(value);
    }
  });
  for(const texture of textures){texture.dispose();texture.source?.data?.close?.();}
  for(const material of materials)material.dispose();
  for(const geometry of geometries)geometry.dispose();
}
function safeAssetURL(value,projectId) {
  const url=new URL(value,location.href);
  if(url.origin!==location.origin || !url.pathname.startsWith('/files/'+encodeURIComponent(projectId)+'/viewport/') || url.username || url.password)throw new Error('实时模型地址无效。');
  return url.href;
}

export class ProductViewport {
  constructor(host,{onChange=()=>{},onError=()=>{},onPick=()=>{},onHover=()=>{},onTraceState}={}) {
    this.host=host;this.onChange=onChange;this.onError=onError;this.onPick=onPick;this.onHover=onHover;this.disposed=false;this.visible=true;this.generation=0;
    this.pickEnabled=false;this.highlighted=new Set();this.raycaster=new THREE.Raycaster();this.pointerDown=null;this.hoveredPart='';this.selectionMaterials=new SelectionMaterials();
    this.librarySlots=new Map();this.previewBindings=[];this.savedLibrarySurfaces=[];
    this.frame=document.createElement('div');this.frame.className='realtime-canvas-frame';host.replaceChildren(this.frame);
    try{this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'high-performance'});}
    catch(_){this.frame.remove();throw new Error('浏览器未能开启 3D 加速。请开启硬件加速后重试，或切换到已渲染图片。');}
    this.renderer.outputColorSpace=THREE.SRGBColorSpace;this.renderer.toneMapping=THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure=1;this.renderer.shadowMap.enabled=true;this.renderer.shadowMap.type=THREE.PCFShadowMap;
    this.renderer.shadowMap.autoUpdate=false;
    const canvas=this.renderer.domElement;canvas.tabIndex=0;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','可交互的产品实时预览。拖动旋转，滚轮缩放，右键拖动平移。方向键旋转，Shift 加方向键平移，加减键缩放，Home 复位。');
    this.frame.append(canvas);this.scene=new THREE.Scene();this.scene.background=new THREE.Color(0xffffff);
    this.refinement=new ViewportRefinement(this.renderer,this.frame);
    this.pathTracing=new ViewportPathTracing(this,onTraceState);
    this.fastOptics=new FastViewportOptics(this);
    this.backdropRevision=0;this.backdrop={id:'white'};
    const environment=studioEnvironmentMap();
    this.environment=this.filterEnvironment(environment);this.scene.environment=this.environment.texture;
    this.studioRadiance=environment;
    this.contextLost=event=>{event.preventDefault();this.stopDrawing();this.onError(new Error('3D 显示连接中断，请重新载入实时预览。'));};
    canvas.addEventListener('webglcontextlost',this.contextLost);
    this.keyHandler=event=>this.onKey(event);canvas.addEventListener('keydown',this.keyHandler);
    this.pointerDownHandler=event=>{if(event.button===0)this.pointerDown={x:event.clientX,y:event.clientY};};
    this.pointerUpHandler=event=>this.onPointerUp(event);
    this.pointerMoveHandler=event=>this.onPointerMove(event);
    this.pointerLeaveHandler=()=>{if(this.hoveredPart){this.hoveredPart='';this.onHover('');}};
    canvas.addEventListener('pointerdown',this.pointerDownHandler);
    canvas.addEventListener('pointerup',this.pointerUpHandler);
    canvas.addEventListener('pointermove',this.pointerMoveHandler);
    canvas.addEventListener('pointerleave',this.pointerLeaveHandler);
    this.resizeObserver=typeof ResizeObserver!=='undefined'?new ResizeObserver(()=>this.resize()):null;this.resizeObserver?.observe(host);
    this.windowResize=()=>this.resize();window.addEventListener('resize',this.windowResize);
    this.pixelRatioChanged=()=>{
      this.pixelRatioQuery?.removeEventListener('change',this.pixelRatioChanged);
      this.pixelRatioQuery=window.matchMedia(`(resolution: ${window.devicePixelRatio||1}dppx)`);
      this.pixelRatioQuery.addEventListener('change',this.pixelRatioChanged);this.resize();
    };
    this.pixelRatioChanged();
    this.visibilityHandler=()=>{if(document.hidden){this.stopDrawing();this.orbiting=false;}else this.requestDraw();};document.addEventListener('visibilitychange',this.visibilityHandler);
  }
  async load(asset,metadata,shot,plan,projectId) {
    const generation=++this.generation;
    if(metadata?.schema!=='wuying-viewport-v1' || metadata.coordinate_system!=='gltf-y-up')throw new Error('实时预览数据版本不兼容，请重试。');
    this.bounds=validateBounds(metadata.bounds);this.metadata=metadata;this.aspect=aspectNumber(plan.aspect);this.planMargin=plan.margin;
    this.shotId=shot?.id;
    this.setExposure(plan);
    const assetURL=safeAssetURL(asset.url,projectId);this.abort=new AbortController();
    const response=await fetch(assetURL,{signal:this.abort.signal,credentials:'same-origin'});
    if(!response.ok)throw new Error('实时模型下载失败，请重试。');
    const buffer=await response.arrayBuffer();
    if(this.disposed || generation!==this.generation)return;
    // Exported GLBs embed all textures. Never follow a model-supplied URL out
    // of this export directory (including arbitrary local /files paths).
    const directory=new URL('.',assetURL).href,manager=new THREE.LoadingManager();
    manager.setURLModifier(value=>{
      if(value.startsWith('blob:') || value.startsWith('data:'))return value;
      const resolved=new URL(value,directory).href;
      if(!resolved.startsWith(directory))throw new Error('模型包含不受支持的外部资源。');
      return resolved;
    });
    const gltf=await new GLTFLoader(manager).parseAsync(buffer,directory);
    if(this.disposed || generation!==this.generation){disposeTree(gltf.scene);return;}
    const nameLookup=new Map();
    for(const rawName of (metadata.product_object_names || [])){
      if(typeof rawName!=='string' || !rawName)continue;
      const withSuffix=rawName+' · viewport';
      nameLookup.set(withSuffix,rawName);
      nameLookup.set(THREE.PropertyBinding.sanitizeNodeName(withSuffix),rawName);
      nameLookup.set(rawName,rawName);
      nameLookup.set(THREE.PropertyBinding.sanitizeNodeName(rawName),rawName);
    }
    const resolveProductName=node=>{
      let cur=node;
      while(cur){
        if(cur.name && nameLookup.has(cur.name))return nameLookup.get(cur.name);
        if(cur.name && /[\s_]·[\s_]viewport$/i.test(cur.name)){
          const stripped=cur.name.replace(/[\s_]·[\s_]viewport$/i,'');
          if(nameLookup.has(stripped))return nameLookup.get(stripped);
          return stripped;
        }
        cur=cur.parent;
      }
      return (node.name || '').replace(/[\s_]·[\s_]viewport$/i,'').replace(/_\d+$/,'');
    };
    gltf.scene.traverse(node=>{
      if(node.isMesh){
        node.userData.productObjectName=resolveProductName(node);
        // Use the GLB's original names. Three can sanitize or uniquify object
        // names, and the material in a checkpoint can be an object-local copy.
        let owner=node;
        while(owner && gltf.parser.associations.get(owner)?.nodes===undefined)owner=owner.parent;
        const nodeIndex=owner && gltf.parser.associations.get(owner)?.nodes;
        const sourceName=gltf.parser.json.nodes?.[nodeIndex]?.name;
        const objectName=metadata.object_names && Object.hasOwn(metadata.object_names,sourceName) ? metadata.object_names[sourceName] : null;
        const slotMap=objectName && metadata.material_slots && Object.hasOwn(metadata.material_slots,objectName) ? metadata.material_slots[objectName] : null;
        if(slotMap){
          node.userData.productObjectName=objectName;
          const mats=Array.isArray(node.material)?node.material:[node.material];
          node.userData.librarySlotKeys=mats.map((mat,index)=>{
            const association=gltf.parser.associations.get(node);
            const primitive=gltf.parser.json.meshes?.[association?.meshes]?.primitives?.[association?.primitives ?? index];
            const materialIndex=gltf.parser.associations.get(mat)?.materials ?? primitive?.material;
            const exportName=gltf.parser.json.materials?.[materialIndex]?.name ?? '';
            if(!Object.hasOwn(slotMap,exportName) || typeof slotMap[exportName]!=='string')return null;
            const materialName=slotMap[exportName],key=JSON.stringify([objectName,materialName]);
            if(!this.librarySlots.has(key))this.librarySlots.set(key,{key,object_name:objectName,material_name:materialName,bindings:[]});
            this.librarySlots.get(key).bindings.push({node,index});
            return key;
          });
        }
        const materials=(Array.isArray(node.material)?node.material:[node.material]).filter(Boolean);
        // Three's real-time shadow maps treat transmission as opaque. Avoid a
        // false black silhouette when the entire mesh is transmissive; mixed
        // material meshes keep their normal shadow rather than lose it all.
        node.castShadow=!materials.length || !materials.every(material=>material.transmission>0.05);
        node.receiveShadow=true;
      }
      if(node.isLight || node.isCamera)node.visible=false;
    });
    assignGeneratedSpaces(gltf.scene,metadata.generated_spaces);
    this.model=gltf.scene;this.scene.add(this.model);this.buildLighting(metadata);
    // A legacy checkpoint may have an optimized camera absent from its older
    // plan. The export manifest measures the actual current scene camera.
    const actual={...shot};
    for(const key of ['projection','azimuth','elevation','focal_length','ortho_scale','target_offset','camera_distance'])if(metadata.camera?.[key]!==undefined)actual[key]=metadata.camera[key];
    if(metadata.camera)actual.viewport_reference_diagonal=this.bounds.diagonal;
    this.setShot(actual,plan);this.backdropDirection=this.camera.getWorldDirection(new THREE.Vector3());
    this.ready=true;this.applyHighlights();this.requestDraw();
  }
  filterEnvironment(texture) {
    if(!this.renderer.extensions.has('OES_texture_float_linear'))texture.minFilter=texture.magFilter=THREE.NearestFilter;
    const filter=new THREE.PMREMGenerator(this.renderer);
    try{return filter.fromEquirectangular(texture);}finally{filter.dispose();}
  }
  clearLighting() {
    this.studioGround?.dispose();
    for(const node of this.studioNodes || []) {
      this.scene.remove(node);
      node.dispose?.();
      node.geometry?.dispose();node.material?.dispose();
    }
    this.studioNodes=null;this.studioGround=null;this.viewFill=null;
  }
  buildLighting(metadata) {
    this.clearLighting();
    this.studioMetadata={...backdropLighting(metadata,this.bounds,this.backdrop),exposure:this.exposureEV ?? metadata.exposure};
    const previous=new Set(this.scene.children);
    buildStudioLighting(this,this.studioMetadata);
    this.studioNodes=this.scene.children.filter(node=>!previous.has(node));
  }
  setExposure(plan) {
    const delta=plan?.shot_adjustments?.[this.shotId]?.exposure_delta ?? 0;
    const ev=Number.isFinite(plan?.exposure) ?
      THREE.MathUtils.clamp(plan.exposure+delta,-4,4) : (this.metadata?.exposure ?? 0);
    if(this.exposureEV===ev)return;
    this.exposureEV=ev;
    this.renderer.toneMappingExposure=2**ev;
    if(this.studioMetadata)this.studioMetadata.exposure=ev;
    if(this.ready)this.requestDraw();
  }
  async setStudioBackdrop(id,preset) {
    const revision=++this.backdropRevision;
    if(this.disposed || id===this.backdrop.id)return;
    const next=await backdropPreset(id,preset);
    if(this.disposed || revision!==this.backdropRevision)return;
    const [photo,environment,lamps]=await Promise.allSettled([loadBackdrop(next),loadStudioEnvironment(next),prepareBackdropLights(next)]);
    const plate=photo.value;
    if(this.disposed || revision!==this.backdropRevision){plate?.dispose();environment.value?.dispose?.();return;}
    if(photo.status==='rejected' || environment.status==='rejected' || lamps.status==='rejected'){
      plate?.dispose();environment.value?.dispose?.();throw photo.reason || environment.reason || lamps.reason;
    }
    const direction=this.backdropDirection || new THREE.Vector3(0,0,-1);
    let radiance,filtered;
    try{
      fitBackdrop(plate,this.aspect);
      radiance=backdropRadiance(next,plate?.image,direction,environment.value);
      filtered=this.filterEnvironment(radiance);
    }catch(error){plate?.dispose();radiance?.dispose();throw error;}
    const oldPlate=this.plate,oldRadiance=this.studioRadiance,oldEnvironment=this.environment;
    this.plate=plate;this.backdrop=next;this.environment=filtered;
    this.studioRadiance=radiance;
    this.scene.background=plate || new THREE.Color(0xffffff);
    this.scene.environment=filtered.texture;
    if(this.model)this.buildLighting(this.metadata);
    this.fastOptics?.invalidate();this.pathTracing?.invalidateBackdrop();
    this.requestDraw();
    oldPlate?.dispose();oldRadiance?.dispose();oldEnvironment?.dispose();
  }
  setShot(shot,plan={},options={}) {
    if(!this.bounds || this.disposed)return;
    this.suppressChange=true;this.aspect=aspectNumber(plan.aspect || this.aspect);this.planMargin=plan.margin ?? this.planMargin;
    const sourceCamera={...this.metadata.camera,aspect:aspectNumber(this.metadata.render?.aspect || plan.aspect)};
    if(options.autoFit)delete sourceCamera.ortho_scale;
    const state=cameraFromShot(shot,this.bounds,this.aspect,sourceCamera,this.planMargin);
    this.controls?.dispose();
    this.camera=state.projection==='orthographic'?new THREE.OrthographicCamera(-state.width/2,state.width/2,state.height/2,-state.height/2,state.near,state.far):new THREE.PerspectiveCamera(state.fov,this.aspect,state.near,state.far);
    this.camera.position.fromArray(state.position);this.camera.up.set(0,1,0);this.camera.lookAt(...state.target);this.focalLength=state.focal_length;
    this.controls=new OrbitControls(this.camera,this.renderer.domElement);this.controls.target.fromArray(state.target);
    this.controls.enableDamping=true;this.controls.dampingFactor=.12;this.controls.minPolarAngle=Math.PI/180*.1;this.controls.maxPolarAngle=Math.PI/180*170;
    this.controls.minZoom=.04;this.controls.maxZoom=40;this.controls.minDistance=this.bounds.diagonal*.02;this.controls.maxDistance=this.bounds.diagonal*100;
    this.controls.rotateSpeed=.75;this.controls.zoomSpeed=.8;this.controls.screenSpacePanning=true;
    this.orbiting=false;
    this.controls.addEventListener('start',()=>{this.orbiting=true;this.requestDraw();});
    this.controls.addEventListener('end',()=>{this.orbiting=false;this.requestDraw();});
    this.controls.update();this.controls.saveState();
    this.initialShot=JSON.parse(JSON.stringify(shot));this.initialPlan={aspect:this.aspect,margin:this.planMargin};
    this.baseline=this.snapshot();this.controls.addEventListener('change',()=>{this.needsDraw=true;if(!this.suppressChange)this.onChange(this.hasChanged());this.requestDraw();});
    this.suppressChange=false;this.resize();this.requestDraw();
  }
  snapshot() {
    return shotFromCamera({projection:this.camera.isOrthographicCamera?'orthographic':'perspective',position:this.camera.position.toArray(),target:this.controls.target.toArray(),width:this.camera.isOrthographicCamera?this.camera.right-this.camera.left:0,zoom:this.camera.zoom,focal_length:this.focalLength},this.bounds);
  }
  hasChanged() {
    if(!this.baseline || !this.camera)return false;
    try{const state=this.snapshot();return Object.keys(state).some(key=>Array.isArray(state[key])?state[key].some((v,i)=>Math.abs(v-(this.baseline[key]?.[i] || 0))>1e-5):typeof state[key]==='number'?Math.abs(state[key]-(this.baseline[key] || 0))>1e-5:state[key]!==this.baseline[key]);}catch(_){return true;}
  }
  acceptView() {this.baseline=this.snapshot();this.initialShot={...this.initialShot,...this.baseline};this.controls.saveState();this.onChange(false);}
  restoreUnappliedView(view) {
    const baseline=this.baseline,initialShot=this.initialShot,initialPlan=this.initialPlan;
    this.setShot(view,initialPlan);this.baseline=baseline;this.initialShot=initialShot;this.initialPlan=initialPlan;this.onChange(this.hasChanged());
  }
  reset() {if(this.initialShot){this.setShot(this.initialShot,this.initialPlan);this.onChange(false);}}
  setTraceOptions(options) {this.pathTracing?.options(options);}
  invalidateTraceScene() {this.fastOptics?.invalidate();this.studioGround?.invalidate();this.renderer.shadowMap.needsUpdate=true;this.pathTracing?.invalidate();this.requestDraw();}
  stopDrawing() {cancelAnimationFrame(this.frameRequest);this.frameRequest=0;this.refinement?.reset();this.pathTracing?.suspend();}
  setVisible(visible) {
    if(this.visible===visible)return;
    this.visible=visible;if(this.controls)this.controls.enabled=visible;
    if(visible){this.resize();this.requestDraw();}else{this.stopDrawing();this.orbiting=false;}
  }
  resize() {
    if(this.disposed || !this.camera)return;
    fitBackdrop(this.plate,this.aspect);
    const available=this.host.getBoundingClientRect(),width=Math.max(1,Math.min(available.width,available.height*this.aspect)),height=width/this.aspect;
    // Allocate one backing pixel per display pixel when settled. The worker
    // traces this exact buffer, so a 128-sample image needs no enlargement.
    // The upper bound also limits the three floating-point accumulation
    // targets on very large displays. Orbiting keeps its smaller raster budget.
    const pixelBudget=this.motionQuality?800000:2048**2;
    const dpr=window.devicePixelRatio||1,ratio=this.motionQuality?Math.min(2,dpr):dpr;
    const physicalWidth=Math.max(1,Math.round(width*ratio)),physicalHeight=Math.max(1,Math.round(height*ratio));
    const scale=Math.min(1,Math.sqrt(pixelBudget/(physicalWidth*physicalHeight)));
    const w=Math.max(1,Math.floor(physicalWidth*scale)),h=Math.max(1,Math.floor(physicalHeight*scale));
    // Only the image seen through glass uses this multisampled color buffer.
    // Keep silhouette, specular and other materials at the full motion size.
    this.renderer.transmissionResolutionScale=this.motionQuality ? .75 : 1;
    this.frame.style.width=width+'px';this.frame.style.height=height+'px';
    if(w===this.renderer.domElement.width && h===this.renderer.domElement.height && this.drawWidth===width && this.drawHeight===height)return;
    this.drawWidth=width;this.drawHeight=height;
    // Set integer physical dimensions directly. Three otherwise floors the
    // canvas and rounds the GL viewport at fractional Windows display scales.
    this.renderer.setDrawingBufferSize(w,h,1);this.requestDraw();
  }
  requestDraw() {
    this.refinement?.reset();
    this.pathTracing?.reset();
    this.needsDraw=true;
    if(this.disposed || !this.visible || document.hidden || this.frameRequest || this.drawing)return;
    const draw=()=>{
      this.frameRequest=0;
      if(this.disposed || !this.visible || document.hidden || !this.camera)return;
      let moved=false;this.drawing=true;
      try{
        // OrbitControls.update() emits change synchronously while damping.
        // The listener may request a draw, but this frame owns scheduling
        // until update/render finish, so only one future RAF can be pending.
        moved=this.controls.update();
        if(this.drawWidth && Boolean(this.motionQuality)!==Boolean(moved || this.orbiting)){
          this.motionQuality=Boolean(moved || this.orbiting);this.resize();
        }
        if(!this.disposed && this.visible && !document.hidden && (this.needsDraw || moved)){
          if(this.viewFill){this.viewFill.position.copy(this.camera.position);this.viewFill.target.position.copy(this.controls.target);}
          this.fastOptics?.prepare(!!this.previewTarget);
          this.needsDraw=false;this.renderer.render(this.scene,this.camera);
          // The host may still be hidden with a 1px backing buffer while the
          // project is loading. Only publish a frame that can actually be seen.
          if(this.ready && this.drawWidth>1 && this.drawHeight>1 &&
              this.frame.dataset.previewOptics!=='ready')this.frame.dataset.previewOptics='ready';
        }
      }finally{this.drawing=false;}
      if(this.disposed || !this.visible || document.hidden || this.frameRequest)return;
      if(moved || this.needsDraw)this.frameRequest=requestAnimationFrame(draw);
      else if(this.ready && !this.orbiting && !this.previewTarget)this.refinement?.start(this.scene,this.camera,()=>this.pathTracing?.start());
    };
    this.frameRequest=requestAnimationFrame(draw);
  }
  onKey(event) {
    if(!this.camera || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','_','Home'].includes(event.key))return;
    event.preventDefault();
    if(event.key==='Home'){this.reset();return;}
    const state=this.snapshot(),b=this.bounds;
    if(event.key==='+' || event.key==='='){if(this.camera.isOrthographicCamera)this.camera.zoom=Math.min(40,this.camera.zoom*1.15);else this.camera.position.lerp(this.controls.target,.12);}
    else if(event.key==='-' || event.key==='_'){if(this.camera.isOrthographicCamera)this.camera.zoom=Math.max(.04,this.camera.zoom/1.15);else this.camera.position.sub(this.controls.target).multiplyScalar(1.15).add(this.controls.target);}
    else if(event.shiftKey){const shift=new THREE.Vector3(event.key==='ArrowLeft'?-1:event.key==='ArrowRight'?1:0,event.key==='ArrowUp'?1:event.key==='ArrowDown'?-1:0,0).applyQuaternion(this.camera.quaternion).multiplyScalar(b.diagonal*.025);this.camera.position.add(shift);this.controls.target.add(shift);}
    else {state.azimuth+=(event.key==='ArrowLeft'?-5:event.key==='ArrowRight'?5:0);state.elevation=Math.max(-80,Math.min(89.9,state.elevation+(event.key==='ArrowUp'?5:event.key==='ArrowDown'?-5:0)));const next=cameraFromShot(state,b,this.aspect,{position:this.camera.position.toArray(),target:this.controls.target.toArray()},this.planMargin);this.camera.position.fromArray(next.position);}
    this.camera.updateProjectionMatrix();this.controls.update();this.onChange(this.hasChanged());this.requestDraw();
  }
  setPickMode(enabled) {
    this.pickEnabled=!!enabled;
    this.renderer.domElement.classList.toggle('is-picking',this.pickEnabled);
    if(!this.pickEnabled && this.hoveredPart){this.hoveredPart='';this.onHover('');}
  }
  setHighlightedParts(names) {
    const next=new Set(Array.from(names || []).filter(Boolean));
    if(next.size===this.highlighted.size && [...next].every(name=>this.highlighted.has(name)))return;
    this.highlighted=next;
    this.applyHighlights();
    this.invalidateTraceScene();
  }
  applyHighlights() {
    if(!this.model)return;
    this.model.traverse(node=>{if(node.isMesh)this.applyNodeHighlight(node);});
  }
  applyNodeHighlight(node){
    const on=this.highlighted.has(node.userData.productObjectName);
    const resolve=m=>this.selectionMaterials.resolve(m,on);
    node.material=Array.isArray(node.material)?node.material.map(resolve):resolve(node.material);
  }
  raycastHits(clientX,clientY) {
    if(!this.ready || !this.model || !this.camera)return [];
    const rect=this.renderer.domElement.getBoundingClientRect();
    if(rect.width<=0 || rect.height<=0 || clientX<rect.left || clientX>rect.right || clientY<rect.top || clientY>rect.bottom)return [];
    const ndc=new THREE.Vector2(((clientX-rect.left)/rect.width)*2-1,-((clientY-rect.top)/rect.height)*2+1);
    this.raycaster.setFromCamera(ndc,this.camera);
    return this.raycaster.intersectObject(this.model,true);
  }
  raycastClient(clientX,clientY) {
    for(const hit of this.raycastHits(clientX,clientY)){
      const name=hit.object?.userData?.productObjectName;
      if(name)return name;
    }
    return '';
  }
  materialTargetAt(x,y) {
    const hit=this.raycastHits(x,y)[0];
    const key=hit?.object.userData.librarySlotKeys?.[hit.face?.materialIndex ?? 0];
    return this.librarySlots.get(key) || null;
  }
  bindLibrarySurface(target,surface){
    const originals=new Map();
    for(const {node,index} of target.bindings){
      if(!originals.has(node)){
        originals.set(node,{node,original:node.material,shadow:node.castShadow});
        if(Array.isArray(node.material))node.material=node.material.slice();
      }
      const material=surface.forGeometry(node.geometry,node.userData.generatedSpace);
      if(Array.isArray(node.material))node.material[index]=material;
      else node.material=material;
    }
    for(const node of originals.keys()){
      const materials=Array.isArray(node.material)?node.material:[node.material];
      node.castShadow=!materials.every(mat=>mat?.transmission>0.05);
    }
    return Array.from(originals.values());
  }
  restoreLibraryBindings(bindings){
    for(const {node,original,shadow} of bindings){
      node.material=original;node.castShadow=shadow;this.applyNodeHighlight(node);
    }
  }
  applySavedLibraryMaterials(overrides,items){
    const byId=new Map(items.map(item=>[item.id,item]));
    for(const row of overrides){
      // The project's own image channels are embedded in this exported GLB.
      if(row.textures && Object.keys(row.textures).length)continue;
      const target=this.librarySlots.get(JSON.stringify([row.object_name,row.material_name]));
      const item=byId.get(row.catalog_id);
      if(!target || !item)continue;
      const surface=makeLibraryPreviewMaterial(item,()=>{
        this.applyHighlights();
        this.invalidateTraceScene();
      },this.bounds.diagonal,row);
      const bindings=this.bindLibrarySurface(target,surface);
      this.savedLibrarySurfaces.push({surface,bindings});
    }
    if(overrides.length){this.applyHighlights();this.invalidateTraceScene();}
  }
  captureMaterialFrame() {
    const source=this.renderer.domElement,image=document.createElement('canvas');
    image.width=source.width;image.height=source.height;
    if(this.pathTracing?.hasFrame)this.pathTracing.present();
    else if(this.refinement?.count)this.refinement.present();else this.renderer.render(this.scene,this.camera);
    image.getContext('2d').drawImage(source,0,0);
    image.setAttribute('aria-label','已保存的部件材质');image.setAttribute('role','img');
    return image;
  }
  previewLibraryMaterial(target,item) {
    if(this.previewTarget===target && this.previewItem===item)return;
    this.restoreLibraryBindings(this.previewBindings);
    this.previewBindings=[];this.previewTarget=target;
    if(item!==this.previewItem){
      this.previewSurface?.dispose();this.previewSurface=null;this.previewItem=item;
      if(item)this.previewSurface=makeLibraryPreviewMaterial(item,()=>this.requestDraw(),this.bounds.diagonal);
    }
    if(target && this.previewSurface)this.previewBindings=this.bindLibrarySurface(target,this.previewSurface);
    this.fastOptics?.refreshBindings();
    this.renderer.shadowMap.needsUpdate=true;
    this.requestDraw();
  }
  onPointerMove(event) {
    if(!this.pickEnabled || !this.ready)return;
    const name=this.raycastClient(event.clientX,event.clientY);
    if(name!==this.hoveredPart){this.hoveredPart=name;this.onHover(name);}
  }
  onPointerUp(event) {
    if(!this.pickEnabled || !this.pointerDown || event.button!==0){this.pointerDown=null;return;}
    const dist=Math.hypot(event.clientX-this.pointerDown.x,event.clientY-this.pointerDown.y);
    this.pointerDown=null;
    if(dist>5)return;
    const name=this.raycastClient(event.clientX,event.clientY);
    if(name)this.onPick(name);
  }
  dispose() {
    if(this.disposed)return;this.disposed=true;this.generation++;this.abort?.abort();this.stopDrawing();this.resizeObserver?.disconnect();this.controls?.dispose();
    this.highlighted.clear();this.previewLibraryMaterial(null,null);this.librarySlots.clear();
    for(const {surface,bindings} of this.savedLibrarySurfaces.reverse()){this.restoreLibraryBindings(bindings);surface.dispose();}
    window.removeEventListener('resize',this.windowResize);document.removeEventListener('visibilitychange',this.visibilityHandler);
    this.pixelRatioQuery?.removeEventListener('change',this.pixelRatioChanged);
    this.renderer.domElement.removeEventListener('webglcontextlost',this.contextLost);this.renderer.domElement.removeEventListener('keydown',this.keyHandler);
    this.renderer.domElement.removeEventListener('pointerdown',this.pointerDownHandler);this.renderer.domElement.removeEventListener('pointerup',this.pointerUpHandler);
    this.renderer.domElement.removeEventListener('pointermove',this.pointerMoveHandler);this.renderer.domElement.removeEventListener('pointerleave',this.pointerLeaveHandler);
    this.fastOptics?.dispose();
    this.applyHighlights();this.selectionMaterials.dispose();
    this.pathTracing?.dispose();this.refinement?.dispose();this.clearLighting();disposeTree(this.scene);this.plate?.dispose();this.studioRadiance?.dispose();this.environment?.dispose();this.renderer.dispose();this.renderer.forceContextLoss();this.frame.remove();
  }
}
window.WuyingViewport={create:(host,options)=>new ProductViewport(host,options),setTraceWarmupEnabled};
window.dispatchEvent(new Event('wuying-viewport-ready'));

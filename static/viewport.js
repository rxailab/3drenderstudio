import * as THREE from 'three';
import { OrbitControls } from './vendor/three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from './vendor/three/examples/jsm/loaders/GLTFLoader.js';
import { cameraFromShot, shotFromCamera, aspectNumber, validateBounds } from './viewport-math.mjs';

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

// A neutral photographic studio for image-based lighting. These cards exist
// only in the environment map: they produce readable reflections on metal and
// glass without covering the product or replacing any imported material.
function createStudioEnvironment() {
  const studio=new THREE.Scene();studio.background=new THREE.Color().setRGB(.32,.32,.32);
  const geometry=new THREE.PlaneGeometry(1,1);
  const cards=[
    {position:[-4,4,3],size:[3.4,5.2],strength:3.8},
    // A low front-side card catches shallow camera angles on horizontal metal.
    {position:[5,2,5],size:[4,4.5],strength:3.4},
    {position:[5,2,-5],size:[3,4.5],strength:2.1},
    {position:[-5,2,5],size:[3,4.5],strength:2.1},
    // The overhead card extends toward the viewer, so upward-facing product
    // surfaces reflect a broad softbox instead of a flat dark ceiling.
    {position:[0,5,2],size:[8,6],strength:4.2},
    {position:[0,2,-5],size:[4.5,3],strength:1.1},
  ];
  for(const card of cards){
    const color=new THREE.Color().setRGB(card.strength,card.strength,card.strength);
    const material=new THREE.MeshBasicMaterial({color,side:THREE.DoubleSide,toneMapped:false});
    const panel=new THREE.Mesh(geometry,material);panel.position.fromArray(card.position);
    panel.scale.set(card.size[0],card.size[1],1);panel.lookAt(0,0,0);studio.add(panel);
  }
  return studio;
}

export class ProductViewport {
  constructor(host,{onChange=()=>{},onError=()=>{}}={}) {
    this.host=host;this.onChange=onChange;this.onError=onError;this.disposed=false;this.visible=true;this.generation=0;
    this.frame=document.createElement('div');this.frame.className='realtime-canvas-frame';host.replaceChildren(this.frame);
    try{this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'high-performance'});}
    catch(_){this.frame.remove();throw new Error('浏览器未能开启 3D 加速。请开启硬件加速后重试，或切换到已渲染图片。');}
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
    this.renderer.outputColorSpace=THREE.SRGBColorSpace;this.renderer.toneMapping=THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure=1;this.renderer.shadowMap.enabled=true;this.renderer.shadowMap.type=THREE.PCFShadowMap;
    const canvas=this.renderer.domElement;canvas.tabIndex=0;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','可交互的产品实时预览。拖动旋转，滚轮缩放，右键拖动平移。方向键旋转，Shift 加方向键平移，加减键缩放，Home 复位。');
    this.frame.append(canvas);this.scene=new THREE.Scene();this.scene.background=new THREE.Color(0xffffff);
    const environment=createStudioEnvironment(),pmrem=new THREE.PMREMGenerator(this.renderer);
    this.environment=pmrem.fromScene(environment,.04);this.scene.environment=this.environment.texture;this.scene.environmentIntensity=.65;
    disposeTree(environment);pmrem.dispose();
    this.contextLost=event=>{event.preventDefault();this.onError(new Error('3D 显示连接中断，请重新载入实时预览。'));};
    canvas.addEventListener('webglcontextlost',this.contextLost);
    this.keyHandler=event=>this.onKey(event);canvas.addEventListener('keydown',this.keyHandler);
    this.resizeObserver=typeof ResizeObserver!=='undefined'?new ResizeObserver(()=>this.resize()):null;this.resizeObserver?.observe(host);
    this.windowResize=()=>this.resize();window.addEventListener('resize',this.windowResize);
    this.visibilityHandler=()=>{if(!document.hidden)this.requestDraw();};document.addEventListener('visibilitychange',this.visibilityHandler);
  }
  async load(asset,metadata,shot,plan,projectId) {
    const generation=++this.generation;
    if(metadata?.schema!=='wuying-viewport-v1' || metadata.coordinate_system!=='gltf-y-up')throw new Error('实时预览数据版本不兼容，请重试。');
    this.bounds=validateBounds(metadata.bounds);this.metadata=metadata;this.aspect=aspectNumber(plan.aspect);this.planMargin=plan.margin;
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
    gltf.scene.traverse(node=>{
      if(node.isMesh){
        const materials=(Array.isArray(node.material)?node.material:[node.material]).filter(Boolean);
        // Three's real-time shadow maps treat transmission as opaque. Avoid a
        // false black silhouette when the entire mesh is transmissive; mixed
        // material meshes keep their normal shadow rather than lose it all.
        node.castShadow=!materials.length || !materials.every(material=>material.transmission>0.05);
        node.receiveShadow=true;
      }
      if(node.isLight || node.isCamera)node.visible=false;
    });
    this.model=gltf.scene;this.scene.add(this.model);this.buildLighting(metadata);
    // A legacy checkpoint may have an optimized camera absent from its older
    // plan. The export manifest measures the actual current scene camera.
    const actual={...shot};
    for(const key of ['projection','azimuth','elevation','focal_length','ortho_scale','target_offset','camera_distance'])if(metadata.camera?.[key]!==undefined)actual[key]=metadata.camera[key];
    this.setShot(actual,plan);this.ready=true;this.requestDraw();
  }
  buildLighting(metadata) {
    const b=this.bounds,d=b.diagonal,c=new THREE.Vector3(...b.center);
    const worldStrength=Number.isFinite(metadata.world?.strength)?Math.max(0,metadata.world.strength):1;
    // Bounded studio approximation retains absolute brightness changes. Do
    // not divide powers by their maximum: that cancels a user's global boost.
    // Blender area-light power and the generated room environment are not in
    // the same units. Keep a neutral preview fill so dark sides remain legible
    // as the model rotates, while still responding to the scene environment.
    this.scene.environmentIntensity=Math.min(8,.55+worldStrength*.65);
    this.renderer.toneMappingExposure=2**Math.max(-10,Math.min(10,Number(metadata.exposure)||0));
    this.scene.add(new THREE.HemisphereLight(0xffffff,0xe3e5e8,Math.min(8,.75+worldStrength*.65)));
    // A bounded camera-side fill keeps faces legible while orbiting; the
    // scene's own lights still supply its main direction and shadows.
    this.viewFill=new THREE.DirectionalLight(0xffffff,Math.min(1.4,.65+worldStrength*.25));
    this.scene.add(this.viewFill,this.viewFill.target);
    const candidates=(metadata.lights || []).filter(light=>Array.isArray(light.position) && light.position.length===3).slice(0,6);
    const defaults=[{position:[c.x-d,c.y+d*1.7,c.z+d*1.5],energy:3},{position:[c.x+d,c.y+d*.7,c.z+d],energy:1.5},{position:[c.x,c.y+d,c.z-d],energy:1.2}];
    const lights=candidates.length?candidates:defaults;
    lights.forEach((source,index)=>{
      const color=Array.isArray(source.color)?new THREE.Color().setRGB(...source.color.slice(0,3)):0xffffff;
      const energy=Math.max(0,Number(source.energy ?? source.power ?? source.intensity)||0);
      const distanceSquared=source.position.reduce((sum,value,i)=>sum+(value-b.center[i])**2,0);
      const intensity=!candidates.length?energy:source.type==='SUN'?energy*2:energy*.06/Math.max(distanceSquared,d*d*.04);
      const light=new THREE.DirectionalLight(color,Math.min(25,intensity));light.position.fromArray(source.position);
      light.target.position.fromArray(Array.isArray(source.target)?source.target:Array.isArray(source.direction)?source.position.map((value,i)=>value+source.direction[i]*d):b.center);this.scene.add(light,light.target);
      if(index===0){light.castShadow=true;light.shadow.mapSize.set(1024,1024);Object.assign(light.shadow.camera,{left:-d,right:d,top:d,bottom:-d,near:Math.max(d*.001,.00001),far:d*30});light.shadow.bias=-.0001;light.shadow.normalBias=d*.001;}
    });
    const ground=new THREE.Mesh(new THREE.PlaneGeometry(d*20,d*20),new THREE.ShadowMaterial({color:0x515761,opacity:.22,depthWrite:false}));
    ground.rotation.x=-Math.PI/2;ground.position.set(c.x,b.min[1]-d*.0005,c.z);ground.receiveShadow=true;this.scene.add(ground);
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
  setVisible(visible) {this.visible=visible;this.controls && (this.controls.enabled=visible);if(visible){this.resize();this.requestDraw();}else{cancelAnimationFrame(this.frameRequest);this.frameRequest=0;}}
  resize() {
    if(this.disposed || !this.camera)return;
    const available=this.host.getBoundingClientRect(),width=Math.max(1,Math.min(available.width,available.height*this.aspect)),height=width/this.aspect;
    this.frame.style.width=width+'px';this.frame.style.height=height+'px';this.renderer.setSize(Math.round(width),Math.round(height),false);this.requestDraw();
  }
  requestDraw() {
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
        if(!this.disposed && this.visible && !document.hidden && (this.needsDraw || moved)){
          if(this.viewFill){this.viewFill.position.copy(this.camera.position);this.viewFill.target.position.copy(this.controls.target);}
          this.needsDraw=false;this.renderer.render(this.scene,this.camera);
        }
      }finally{this.drawing=false;}
      if((moved || this.needsDraw) && !this.disposed && this.visible && !document.hidden && !this.frameRequest)this.frameRequest=requestAnimationFrame(draw);
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
  dispose() {
    if(this.disposed)return;this.disposed=true;this.generation++;this.abort?.abort();cancelAnimationFrame(this.frameRequest);this.resizeObserver?.disconnect();this.controls?.dispose();
    window.removeEventListener('resize',this.windowResize);document.removeEventListener('visibilitychange',this.visibilityHandler);
    this.renderer.domElement.removeEventListener('webglcontextlost',this.contextLost);this.renderer.domElement.removeEventListener('keydown',this.keyHandler);
    disposeTree(this.scene);this.environment?.dispose();this.renderer.dispose();this.renderer.forceContextLoss();this.frame.remove();
  }
}
window.WuyingViewport={create:(host,options)=>new ProductViewport(host,options)};
window.dispatchEvent(new Event('wuying-viewport-ready'));

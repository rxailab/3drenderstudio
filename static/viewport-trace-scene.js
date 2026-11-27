import { addStudioLight } from './viewport-light.js';
import * as THREE from './vendor/three/build/three.module.js';
import {studioEnvironmentMap} from './viewport-studio.js';

const materialScalars = [
  'metalness', 'roughness', 'transmission', 'ior', 'thickness', 'attenuationDistance',
  'clearcoat', 'clearcoatRoughness', 'sheen', 'sheenRoughness', 'specularIntensity',
  'iridescence', 'iridescenceIOR', 'iridescenceThicknessMinimum', 'iridescenceThicknessMaximum',
  'emissiveIntensity', 'opacity', 'alphaTest', 'flatShading',
  'transparent', 'vertexColors', 'side', 'bumpScale', 'normalMapType',
];
const colorFields = ['color', 'attenuationColor', 'emissive', 'sheenColor', 'specularColor'];
const textureFields = [
  'map', 'roughnessMap', 'metalnessMap', 'normalMap', 'bumpMap', 'alphaMap',
  'transmissionMap', 'emissiveMap', 'clearcoatMap', 'clearcoatRoughnessMap',
  'clearcoatNormalMap', 'sheenColorMap', 'sheenRoughnessMap', 'specularColorMap',
  'specularIntensityMap', 'iridescenceMap', 'iridescenceThicknessMap',
];
const uvNames=['uv','uv1','uv2','uv3'];

// The path tracer packs material maps in equally sized array layers. Keep
// their actual dimensions when possible, with a shared 12 Mtexel budget.
// The camera's backplate and environment use separate textures.
export function traceTextureLayout(materials) {
  const textures=new Set();
  let width=1,height=1;
  for(const material of materials) for(const field of textureFields) {
    const map=material?.[field], image=map?.image;
    if(!map?.isTexture || !image?.width || !image.height || textures.has(map))continue;
    textures.add(map);
    width=Math.max(width,image.width);
    height=Math.max(height,image.height);
  }
  const layers=Math.max(1,textures.size);
  const scale=Math.min(1,4096/Math.max(width,height),Math.sqrt(12e6/(width*height*layers)));
  return {width:Math.max(1,Math.floor(width*scale)),height:Math.max(1,Math.floor(height*scale)),layers};
}

// Transferred buffers and bitmaps are independent copies. Raster interaction can
// continue to use every original mesh and texture while the worker builds its BVH.
export async function serializeTraceScene(model, metadata, bounds, {signal, environment, backplate, materialLayout} = {}) {
  const meshes = [], geometries = [], materials = [], textures = [], transfer = [];
  const geometryIds = new Map(), materialIds = new Map(), textureIds = new Map();
  const sourceMeshes = [], bitmaps = [], attributes = new Map();
  let uvChannels;
  let lastYield = performance.now();
  function abortIfNeeded() {
    if (signal?.aborted) throw new DOMException('Scene preparation canceled', 'AbortError');
  }
  async function yieldToInteraction() {
    if (performance.now()-lastYield>6) {
      await new Promise(resolve=>setTimeout(resolve,0));
      abortIfNeeded();
      lastYield=performance.now();
    }
  }
  async function copyArray(array) {
    const copy=new array.constructor(array.length);
    const chunk=Math.floor(4*1024*1024/array.BYTES_PER_ELEMENT);
    for (let start=0;start<array.length;start+=chunk) {
      await yieldToInteraction();
      copy.set(array.subarray(start,Math.min(start+chunk,array.length)),start);
    }
    transfer.push(copy.buffer);
    return copy;
  }
  async function attribute(a) {
    // Interleaved GLBs share one underlying array across several attributes.
    // Preserve that layout on the wire instead of deinterleaving on the UI thread.
    const source = a.isInterleavedBufferAttribute ? a.data.array : a.array;
    if (!attributes.has(source)) attributes.set(source, await copyArray(source));
    return {array:attributes.get(source), size:a.itemSize, normalized:a.normalized,
      stride:a.isInterleavedBufferAttribute ? a.data.stride : 0, offset:a.offset || 0};
  }
  async function geometryId(g) {
    if (geometryIds.has(g)) return geometryIds.get(g);
    const id = geometries.length, attrs = {};
    for (const name of ['position','normal',...uvChannels.map(i=>uvNames[i]),'tangent','color']) {
      if (g.attributes[name]) attrs[name] = await attribute(g.attributes[name]);
    }
    geometries.push({attrs,index:g.index ? await attribute(g.index) : null,
      groups:g.groups.map(group=>({...group})),drawRange:{...g.drawRange}});
    geometryIds.set(g,id);
    return id;
  }
  async function textureId(t,maxWidth=materialLayout.width,maxHeight=materialLayout.height) {
    if (textureIds.has(t)) return textureIds.get(t);
    const image = t.image;
    if (!image?.width || !image.height) return null;
    let bitmap, data;
    if (ArrayBuffer.isView(image.data)) {
      data = await copyArray(image.data);
    } else {
      // WebGL ignores unpack flipY for ImageBitmap. Apply the source's
      // orientation here when converting an HTMLImageElement or canvas.
      const isBitmap = typeof ImageBitmap !== 'undefined' && image instanceof ImageBitmap;
      const ratio = Math.min(1,maxWidth/image.width,maxHeight/image.height);
      const options = {imageOrientation:!isBitmap && t.flipY ? 'flipY' : 'none',
        premultiplyAlpha:'none', colorSpaceConversion:'none'};
      if (ratio<1) Object.assign(options,{resizeWidth:Math.max(1,Math.round(image.width*ratio)),
        resizeHeight:Math.max(1,Math.round(image.height*ratio)),resizeQuality:'high'});
      bitmap = await createImageBitmap(image,options);
      bitmaps.push(bitmap);
      transfer.push(bitmap);
      abortIfNeeded();
    }
    if (t.matrixAutoUpdate) t.updateMatrix();
    const id = textures.length;
    textures.push({bitmap,data,width:bitmap?.width ?? image.width,height:bitmap?.height ?? image.height,type:t.type,format:t.format,
      channel:Math.max(0,uvChannels.indexOf(t.channel || 0)),flipY:!!data && t.flipY,colorSpace:t.colorSpace,wrapS:t.wrapS,wrapT:t.wrapT,
      rotation:t.rotation,repeat:t.repeat.toArray(),offset:t.offset.toArray(),center:t.center.toArray(),
      matrix:t.matrix.toArray(),matrixAutoUpdate:t.matrixAutoUpdate});
    textureIds.set(t,id);
    return id;
  }
  async function materialId(m) {
    if (materialIds.has(m)) return materialIds.get(m);
    const id = materials.length;
    const row = {name:m.name, textures:{}, fields:{}, colors:{}};
    for (const field of materialScalars) if (field in m) row.fields[field] = m[field];
    for (const field of colorFields) if (m[field]?.isColor) row.colors[field] = m[field].toArray();
    for (const field of ['normalScale','clearcoatNormalScale']) {
      if (m[field]) row[field] = m[field].toArray();
    }
    if (m.isMeshBasicMaterial) {
      row.colors.emissive = row.colors.color;
      row.colors.color = [0,0,0];
      row.fields.emissiveIntensity = 1;
      row.fields.specularIntensity = 0;
    }
    if (!m.visible) Object.assign(row.fields,{transparent:true,opacity:0});
    // The catalog uses custom vertex box projection and ranges for the raster
    // shader. The worker bakes equivalent UVs and texture values for ray hits.
    row.catalog = m.userData?.wuyingTrace || null;
    for (const field of textureFields) {
      if (m[field]?.isTexture) {
        const tex = await textureId(m[field]);
        if (tex !== null) row.textures[field] = tex;
      }
    }
    if (m.isMeshBasicMaterial && row.textures.map !== undefined) row.textures.emissiveMap = row.textures.map;
    materialIds.set(m,id);
    materials.push(row);
    return id;
  }
  try {
    abortIfNeeded();
    model.updateWorldMatrix(true,true);
    model.traverseVisible(mesh=>{if (mesh.isMesh && mesh.geometry?.attributes.position) sourceMeshes.push(mesh);});
    const sourceMaterials=new Set(sourceMeshes.flatMap(mesh=>mesh.material));
    if(!materialLayout)materialLayout=traceTextureLayout(sourceMaterials);
    // Pick only the coordinate sets that the materials actually sample. A
    // model whose textures all use TEXCOORD_1 needs just one GPU UV layer.
    const channels=new Set();
    for(const material of sourceMaterials) {
      for(const field of ['normalMap',...textureFields]) {
        const tex=material?.[field];
        if(tex?.isTexture)channels.add(tex.channel || 0);
      }
    }
    uvChannels=channels.size?[...channels]:[0];
    for (const mesh of sourceMeshes) {
      abortIfNeeded();
      const rows = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      if (!rows.every(Boolean) || rows.every(m=>!m.visible)) continue;
      const ids = [];
      for (const mat of rows) ids.push(await materialId(mat));
      const geometry = await geometryId(mesh.geometry);
      if (mesh.isInstancedMesh) {
        const transform = new THREE.Matrix4();
        for (let index=0;index<mesh.count;index++) {
          mesh.getMatrixAt(index,transform);
          meshes.push({geometry,materials:ids,matrix:transform.premultiply(mesh.matrixWorld).toArray()});
        }
      } else {
        meshes.push({geometry,materials:ids,matrix:mesh.matrixWorld.toArray()});
      }
      await yieldToInteraction();
    }
    const env = environment?.isDataTexture ? await textureId(environment,environment.image.width,environment.image.height) : null;
    const plate = backplate ? await textureId(backplate,2048,2048) : null;
    abortIfNeeded();
    return {payload:{meshes,geometries,materials,textures,bounds,uvChannels,
      lights:metadata.lights || [],world:metadata.world || {},floorColor:metadata.floorColor,
      groundShadow:metadata.groundShadow,exposure:metadata.exposure || 0,env,plate},transfer};
  } catch (error) {
    for (const bitmap of bitmaps) bitmap.close();
    throw error;
  }
}

function unpackTexture(t) {
  const tex = t.data ? new THREE.DataTexture(t.data,t.width,t.height,t.format,t.type) : new THREE.Texture(t.bitmap);
  Object.assign(tex,{channel:t.channel ?? 0,colorSpace:t.colorSpace,flipY:t.flipY,wrapS:t.wrapS,wrapT:t.wrapT,
    rotation:t.rotation,matrixAutoUpdate:t.matrixAutoUpdate,minFilter:THREE.LinearFilter,magFilter:THREE.LinearFilter});
  tex.repeat.fromArray(t.repeat);
  tex.offset.fromArray(t.offset);
  tex.center.fromArray(t.center);
  tex.matrix.fromArray(t.matrix);
  tex.needsUpdate=true;
  return tex;
}

function derivedMap(source,mode,amount) {
  const image=source.image, w=image.width, h=image.height;
  let pixels=image.data;
  if (!pixels) {
    const ctx=new OffscreenCanvas(w,h).getContext('2d',{willReadFrequently:true});
    ctx.drawImage(image,0,0);
    pixels=ctx.getImageData(0,0,w,h).data;
  }
  const components=pixels.length/(w*h);
  const channel=mode==='roughness' ? Math.min(1,components-1) : 0;
  const sample=(x,y)=>{
    const value=pixels[(y*w+x)*components+channel];
    if (source.type===THREE.FloatType) return value;
    if (source.type===THREE.HalfFloatType) return THREE.DataUtils.fromHalfFloat(value);
    return value/(pixels.BYTES_PER_ELEMENT===2 ? 65535 : 255);
  };
  const data=new Uint8Array(w*h*4);
  for (let y=0;y<h;y++) for (let x=0;x<w;x++) {
    const i=(y*w+x)*4;
    if (mode==='roughness') {
      const r=Math.round(255*THREE.MathUtils.clamp(amount[0]+sample(x,y)*(amount[1]-amount[0]),0,1));
      data[i]=r;data[i+1]=r;data[i+2]=r;
    } else {
      const dx=(sample((x+1)%w,y)-sample((x+w-1)%w,y))*w*amount*.5;
      const dy=(sample(x,(y+1)%h)-sample(x,(y+h-1)%h))*h*amount*.5;
      const length=Math.hypot(dx,dy,1);
      data[i]=Math.round(127.5*(1-dx/length));
      data[i+1]=Math.round(127.5*(1-dy/length));
      data[i+2]=Math.round(127.5*(1+1/length));
    }
    data[i+3]=255;
  }
  const tex=new THREE.DataTexture(data,w,h);
  tex.channel=source.channel;
  tex.wrapS=source.wrapS;tex.wrapT=source.wrapT;tex.flipY=source.flipY;
  tex.matrix.copy(source.matrix);tex.matrixAutoUpdate=false;
  tex.minFilter=tex.magFilter=THREE.LinearFilter;tex.needsUpdate=true;
  return tex;
}

function unpackMaterial(row,textures,owned) {
  const m=new THREE.MeshPhysicalMaterial(row.fields);
  m.name=row.name;
  for (const [field,rgb] of Object.entries(row.colors)) m[field].fromArray(rgb);
  for (const [field,index] of Object.entries(row.textures)) m[field]=textures[index];
  for (const field of ['normalScale','clearcoatNormalScale']) if (row[field]) m[field].fromArray(row[field]);
  if (!m.normalMap && m.bumpMap) {
    m.normalMap=derivedMap(m.bumpMap,'bump',m.bumpScale);
    owned.add(m.normalMap);
  }
  if (m.roughnessMap && row.catalog?.roughnessRange) {
    m.roughnessMap=derivedMap(m.roughnessMap,'roughness',row.catalog.roughnessRange);
    owned.add(m.roughnessMap);
  }
  return m;
}

function boxUVs(geometry,matrix,d,attribute) {
  const pos=geometry.getAttribute('position'),normal=geometry.getAttribute('normal');
  const uv=new Float32Array(pos.count*2),p=new THREE.Vector3(),n=new THREE.Vector3();
  const normalMatrix=new THREE.Matrix3().getNormalMatrix(matrix);
  for (let i=0;i<pos.count;i++) {
    p.fromBufferAttribute(pos,i).applyMatrix4(matrix).multiplyScalar(1/d);
    n.fromBufferAttribute(normal,i).applyNormalMatrix(normalMatrix);
    const x=Math.abs(n.x),y=Math.abs(n.y),z=Math.abs(n.z);
    uv[i*2]=x>y && x>z ? p.z : p.x;
    uv[i*2+1]=x>y && x>z || z>=y ? p.y : p.z;
  }
  geometry.setAttribute(attribute,new THREE.BufferAttribute(uv,2));
}

// Blender may export transmission + IOR on a solid without KHR_materials_volume.
// A consistently wound closed boundary gives the tracer two real intersections.
// UV and hard-normal seams can duplicate the same position in a glTF primitive.
export function enclosesVolume(geom) {
  const p=geom.getAttribute('position'),index=geom.index;
  geom.computeBoundingBox();
  const span=geom.boundingBox.getSize(new THREE.Vector3()),origin=geom.boundingBox.min;
  const diagonal=span.length();
  if (Math.min(span.x,span.y,span.z)<=diagonal*1e-7) return false;
  const scale=1e6/diagonal,byPosition=new Map(),vertexId=new Uint32Array(p.count);
  for (let i=0;i<p.count;i++) {
    const x=Math.round((p.getX(i)-origin.x)*scale),y=Math.round((p.getY(i)-origin.y)*scale);
    const z=Math.round((p.getZ(i)-origin.z)*scale),key=x+','+y+','+z;
    if (!byPosition.has(key)) byPosition.set(key,byPosition.size);
    vertexId[i]=byPosition.get(key);
  }
  const unmatched=new Map(),stride=byPosition.size;
  function edge(a,b) {
    const key=Math.min(a,b)*stride+Math.max(a,b),direction=a<b ? 1 : -1;
    const total=(unmatched.get(key) || 0)+direction;
    if (total) unmatched.set(key,total);
    else unmatched.delete(key);
  }
  const count=index?.count ?? p.count;
  for (let i=0;i+2<count;i+=3) {
    const a=vertexId[index ? index.getX(i) : i], b=vertexId[index ? index.getX(i+1) : i+1];
    const c=vertexId[index ? index.getX(i+2) : i+2];
    if (a===b || b===c || c===a) continue;
    edge(a,b);edge(b,c);edge(c,a);
  }
  return unmatched.size===0;
}

export function buildTraceScene(payload) {
  const {bounds}=payload,scene=new THREE.Scene(),owned=new Set();
  const uvChannels=payload.uvChannels || [0];
  const textures=payload.textures.map(unpackTexture);
  const mats=payload.materials.map(row=>unpackMaterial(row,textures,owned));
  const geometries=payload.geometries.map(row=>{
    const geom=new THREE.BufferGeometry();
    function unpack(a) {
      if (!a.stride) return new THREE.BufferAttribute(a.array,a.size,a.normalized);
      return new THREE.InterleavedBufferAttribute(new THREE.InterleavedBuffer(a.array,a.stride),a.size,a.offset,a.normalized);
    }
    for (const [name,attr] of Object.entries(row.attrs)) {
      const sourceChannel=uvNames.indexOf(name);
      const key=sourceChannel===-1?name:uvNames[uvChannels.indexOf(sourceChannel)];
      if (!key) continue;
      let buffer=unpack(attr);
      // The merged geometry includes both the product and a floating-point
      // floor. Convert quantized glTF UVs before merging their backing arrays.
      if(sourceChannel!==-1 && !(attr.array instanceof Float32Array)) {
        const coords=new Float32Array(buffer.count*2);
        for(let i=0;i<buffer.count;i++){coords[i*2]=buffer.getX(i);coords[i*2+1]=buffer.getY(i);}
        buffer=new THREE.BufferAttribute(coords,2);
      }
      geom.setAttribute(key,buffer);
    }
    if (row.index) geom.setIndex(unpack(row.index));
    for (const group of row.groups) geom.addGroup(group.start,group.count,group.materialIndex);
    geom.setDrawRange(row.drawRange.start,row.drawRange.count);
    if (!geom.hasAttribute('normal')) geom.computeVertexNormals();
    return geom;
  });
  const watertight=new Map(),solidVariants=new Map();
  for (const row of payload.meshes) {
    const matrix=new THREE.Matrix4().fromArray(row.matrix);
    let geom=geometries[row.geometry];
    const slots=row.materials.map(i=>{
      const material=mats[i];
      if (!(material.transmission>0) || material.thickness>0 || material.attenuationDistance<Infinity ||
          payload.materials[i].catalog?.thinWalled) return material;
      if (!watertight.has(geom)) watertight.set(geom,enclosesVolume(geom));
      if (!watertight.get(geom)) return material;
      if (!solidVariants.has(material)) {
        const solid=material.clone();
        // three-gpu-pathtracer uses a positive thickness as the volume flag;
        // Beer attenuation and refraction use the measured ray path in world units.
        solid.thickness=1;
        solid.side=THREE.DoubleSide;
        solidVariants.set(material,solid);
        owned.add(solid);
      }
      return solidVariants.get(material);
    });
    // The BVH stores world-space triangles. A shared material can appear on
    // several transformed instances, each with its own world-to-Generated map.
    for(const [slot,id] of row.materials.entries()){
      const wood=payload.materials[id].catalog?.wood;
      if(!wood)continue;
      const variant=slots[slot].clone();
      const transform=new THREE.Matrix4().fromArray(wood.localToGenerated).multiply(matrix.clone().invert());
      variant.userData.wuyingWood={...wood,worldToGenerated:transform.toArray()};
      slots[slot]=variant;
      owned.add(variant);
    }
    const catalogUv=uvNames[uvChannels.indexOf(0)];
    if (catalogUv && !geom.hasAttribute(catalogUv) && row.materials.some(i=>payload.materials[i].catalog) && slots.some(m=>textureFields.some(key=>m[key]))) {
      // Several object instances can share positions while requiring different
      // world-aligned projections. Share their original attributes.
      const projected=new THREE.BufferGeometry();
      projected.index=geom.index;
      for (const [key,attr] of Object.entries(geom.attributes)) projected.setAttribute(key,attr);
      for (const group of geom.groups) projected.addGroup(group.start,group.count,group.materialIndex);
      projected.setDrawRange(geom.drawRange.start,geom.drawRange.count);
      boxUVs(projected,matrix,Math.max(bounds.diagonal,1e-8),catalogUv);
      geom=projected;owned.add(geom);
    }
    const mesh=new THREE.Mesh(geom,slots.length===1 ? slots[0] : slots);
    mesh.matrixAutoUpdate=false;mesh.matrix.copy(matrix);scene.add(mesh);
  }
  // The primary ray leaves coverage for the raster plate and its shadow.
  // Transmitted rays project onto the photographic set behind the product.
  const floorGeometry=new THREE.PlaneGeometry(20*bounds.diagonal,20*bounds.diagonal);
  const floorColor=payload.floorColor?new THREE.Color(payload.floorColor):new THREE.Color().setRGB(.78,.78,.78);
  const floorMaterial=new THREE.MeshStandardMaterial({color:floorColor,roughness:1});
  floorMaterial.matte=true;
  const floor=new THREE.Mesh(floorGeometry,floorMaterial);
  floor.name='Studio shadow receiver';
  floor.rotation.x=-Math.PI/2;
  floor.position.set(bounds.center[0],bounds.min[1]-bounds.diagonal*.0005,bounds.center[2]);
  scene.add(floor);
  owned.add(floorGeometry);owned.add(floorMaterial);
  for(let i=0;i<textures.length;i++)if(i!==payload.env && i!==payload.plate)owned.add(textures[i]);
  for(const obj of [...mats,...geometries])owned.add(obj);
  const rig=new THREE.Group();
  scene.add(rig);
  function clearStudio() {
    rig.clear();
    scene.environment?.dispose();
    scene.background?.dispose();
    scene.background?.image?.close?.();
  }
  function updateStudio(studio,images=studio.textures.map(unpackTexture)) {
    clearStudio();
    scene.background=studio.plate==null?null:images[studio.plate];
    scene.environment=studio.env==null?studioEnvironmentMap(studio.world):images[studio.env];
    scene.environment.mapping=THREE.EquirectangularReflectionMapping;
    scene.environmentIntensity=Math.max(0,studio.world.studioIntensity ?? studio.world.strength ?? 1);
    if(studio.floorColor)floorMaterial.color.set(studio.floorColor);
    else floorMaterial.color.setRGB(.78,.78,.78);
    // Matte primary hits leave coverage for the photographed floor. Masking
    // the same two triangles for secondary/shadow rays avoids a BVH rebuild
    // when a preset disables the ground, including the headphone example.
    floorMaterial.alphaTest=.5;
    floorMaterial.opacity=studio.groundShadow===0?0:1;
    floorMaterial.castShadow=studio.groundShadow!==0;
    for(const light of studio.lights)addStudioLight(rig,light,bounds);
    rig.updateMatrixWorld(true);
  }
  updateStudio(payload,textures);
  scene.updateMatrixWorld(true);
  return {scene,bounds,updateStudio,uvAttributes:uvNames.slice(0,uvChannels.length),materialLayout:traceTextureLayout(mats),dispose(){
    clearStudio();
    for (const item of owned) item.dispose();
    for(let i=0;i<payload.textures.length;i++)if(i!==payload.plate)payload.textures[i].bitmap?.close();
    scene.clear();
  }};
}

// In the pinned WebGLPathTracer, primary misses and refracted misses share a
// background-alpha assignment. A ray which already hit glass must continue to
// own its pixel when the transparent photographic backdrop is composited.
export function configureTraceBackdrop(tracer) {
  const sharedAssignment='gl_FragColor.a = backgroundAlpha;';
  const mats=new Set([tracer._pathTracer.material,tracer._lowResPathTracer.material]);
  for (const mat of mats) {
    if (!mat.fragmentShader.includes(sharedAssignment)) throw new Error('不兼容的路径追踪背景着色器');
    mat.uniforms.wyPlatePlane={value:new THREE.Vector4()};
    mat.uniforms.wyPlateCrop={value:new THREE.Vector4(1,1,0,0)};
    mat.uniforms.wyWorldToClip={value:new THREE.Matrix4()};
    mat.fragmentShader=mat.fragmentShader
      .replace('vec3 sampleBackground( vec3 direction, vec2 uv ) {',`
        #if FEATURE_BACKGROUND_MAP
        uniform vec4 wyPlatePlane, wyPlateCrop;
        uniform mat4 wyWorldToClip;
        vec3 wyTransmittedPlate(vec3 origin,vec3 direction) {
          vec3 room=environmentIntensity*sampleEquirectColor(envMapInfo.map,envRotation3x3*direction);
          float facing=dot(wyPlatePlane.xyz,direction);
          if(facing<.001)return room;
          float distanceToPlate=-(dot(wyPlatePlane.xyz,origin)+wyPlatePlane.w)/facing;
          if(distanceToPlate<=0.)return room;
          vec4 clip=wyWorldToClip*vec4(origin+distanceToPlate*direction,1.);
          vec2 uv=clip.xy/clip.w*.5+.5;
          float edge=max(abs(uv.x-.5),abs(uv.y-.5));
          return mix(texture2D(backgroundMap,uv*wyPlateCrop.xy+wyPlateCrop.zw).rgb,room,smoothstep(.50,.66,edge));
        }
        #endif
        vec3 sampleBackground( vec3 direction, vec2 uv ) {`)
      .replace('sampleBackground( ray.direction, rand2( 2 ) ) * state.throughputColor;',`
        #if FEATURE_BACKGROUND_MAP
          wyTransmittedPlate(ray.origin,ray.direction)
        #else
          sampleBackground(ray.direction,rand2(2))
        #endif
        * state.throughputColor;`)
      .replace(sharedAssignment,'if (state.firstRay) { gl_FragColor = vec4(0.0); } else { gl_FragColor.a = 1.0; }')
      .replace('if ( material.matte && state.firstRay ) {',`
        #if FEATURE_BACKGROUND_MAP
        if(material.matte && state.transmissiveRay && !state.firstRay) {
          gl_FragColor.rgb+=wyTransmittedPlate(ray.origin,ray.direction)*state.throughputColor;
          break;
        }
        #endif
        if ( material.matte && state.firstRay ) {`);
    mat.needsUpdate=true;
  }
}

export function alignTraceBackdrop(material,camera,handle) {
  const plate=handle.scene.background;
  if(!plate)return;
  const {center,diagonal}=handle.bounds, forward=camera.getWorldDirection(new THREE.Vector3());
  const rear=new THREE.Vector3(...center).addScaledVector(forward,.92*diagonal);
  const u=material.uniforms;
  u.wyPlatePlane.value.set(forward.x,forward.y,forward.z,-forward.dot(rear));
  u.wyWorldToClip.value.multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse);
  const aspect=camera.projectionMatrix.elements[5]/camera.projectionMatrix.elements[0];
  const imageAspect=plate.image.width/plate.image.height;
  const width=Math.min(1,aspect/imageAspect),height=Math.min(1,imageAspect/aspect);
  u.wyPlateCrop.value.set(width,height,(1-width)/2,(1-height)/2);
}

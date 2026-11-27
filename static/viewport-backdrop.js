import caseLights from './studio-backdrops/lights.json' with { type: 'json' };
import * as THREE from './vendor/three/build/three.module.js';
import { studioEnvironmentMap } from './viewport-studio.js';

const white = {id:'white'};
let catalog;

export async function backdropPreset(id, supplied) {
  if (id === 'white') return white;
  if (supplied?.id === id) return supplied;
  if (!catalog) {
    catalog = fetch(new URL('./studio-backdrops/catalog.json', import.meta.url))
      .then(response => {
        if (!response.ok) throw new Error('场景背景目录读取失败。');
        return response.json();
      }).catch(error => {catalog = null; throw error;});
  }
  const preset = (await catalog).presets.find(row => row.id === id);
  if (!preset) throw new Error('找不到所选场景背景。');
  return preset;
}

export function fitBackdrop(texture, aspect) {
  if (!texture || !aspect) return;
  const imageAspect = texture.image.width / texture.image.height;
  texture.repeat.set(Math.min(1, aspect / imageAspect), Math.min(1, imageAspect / aspect));
  texture.offset.set((1 - texture.repeat.x) / 2, (1 - texture.repeat.y) / 2);
  texture.updateMatrix();
}

export async function loadBackdrop(preset) {
  if (preset.id === 'white') return null;
  const url = new URL(preset.image, location.href);
  if (url.origin !== location.origin || !url.pathname.startsWith('/static/studio-backdrops/')) {
    throw new Error('场景背景地址无效。');
  }
  try {
    const texture = await new THREE.TextureLoader().loadAsync(url.href);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  } catch {
    throw new Error('场景背景图片读取失败，请重新选择。');
  }
}

const envWidth=128,envHeight=64;

export async function loadStudioEnvironment(preset) {
  const source=preset.radianceMap || preset.environmentMap;
  if(!source)return null;
  const url=new URL(source,location.href), folder=preset.radianceMap?'radiance':'environments';
  const base='/static/studio-backdrops/';
  if(url.origin!==location.origin || ![base,base+'builtin/'].some(root=>url.pathname.startsWith(root+folder+'/')))throw new Error('摄影棚环境地址无效。');
  const response=await fetch(url,{credentials:'same-origin'});
  if(!response.ok)throw new Error('摄影棚环境读取失败，请重新选择。');
  const bytes=await response.arrayBuffer();
  if(preset.radianceMap) {
    if(bytes.byteLength!==256*128*3*2)throw new Error('环境光数据不完整。');
    const half=new Uint16Array(bytes), rgba=new Float32Array(256*128*4);
    for(let i=0,j=0;i<half.length;i+=3,j+=4){
      for(let c=0;c<3;c++)rgba[j+c]=THREE.DataUtils.fromHalfFloat(half[i+c]);
      rgba[j+3]=1;
    }
    const texture=new THREE.DataTexture(rgba,256,128,THREE.RGBAFormat,THREE.FloatType);
    texture.mapping=THREE.EquirectangularReflectionMapping;
    texture.colorSpace=THREE.LinearSRGBColorSpace;
    texture.minFilter=texture.magFilter=THREE.LinearFilter;texture.needsUpdate=true;
    return texture;
  }
  if(bytes.byteLength!==envWidth*envHeight*3*4)throw new Error('摄影棚环境数据不完整。');
  return new Float32Array(bytes);
}

function measuredRadiance(rgb,preset) {
  // The archived RGBE panoramas start at the north pole. These DataTextures
  // are sampled from the south pole, with the rig's yaw applied in world space.
  const lighting=preset.lighting || {}, addCards=preset.id!=='welcome' && !caseLights[preset.id];
  const texture=studioEnvironmentMap({},{...lighting,cards:addCards});
  const {data,width,height}=texture.image;
  const turn=(preset.environmentRotation ?? lighting.yaw ?? 0)/360, gain=preset.environmentGain ?? 1;
  for(let y=0;y<height;y++){
    const row=envHeight-1-Math.floor(y*envHeight/height);
    for(let x=0;x<width;x++){
      const u=(x+.5)/width+turn;
      const col=Math.floor((u-Math.floor(u))*envWidth);
      const measured=(row*envWidth+col)*3,i=(y*width+x)*4;
      // The tutorial rigs already illuminate surfaces using their emitters.
      // Generic photo sets use the studio cards to soften reflections.
      const panel=addCards?Math.max(0,data[i]-.66)*.17:0;
      for(let c=0;c<3;c++)data[i+c]=rgb[measured+c]*gain+panel;
    }
  }
  texture.needsUpdate=true;
  return texture;
}

// Backplates are camera-space photographs. Build a separate, low-resolution
// reflection environment: the photographic set behind the subject, dark flags
// on its sides, colored bounce from the floor and luminous studio cards.
export function backdropRadiance(preset, image, cameraDirection, measured) {
  if(measured?.isDataTexture)return measured;
  if(measured)return measuredRadiance(measured,preset);
  if (!image) return studioEnvironmentMap();
  const width = 128, height = 64;
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d', {willReadFrequently:true});
  ctx.drawImage(image, 0, 0, width, height);
  return radianceFromPhoto(preset,ctx.getImageData(0, 0, width, height).data,cameraDirection);
}

export function radianceFromPhoto(preset,photo,cameraDirection) {
  const width=128,height=64;
  const linear = Array.from({length:256}, (_, i) => new THREE.Color().setRGB(i/255,i/255,i/255,THREE.SRGBColorSpace).r);
  const ambient = new THREE.Color(preset.environmentColor).toArray();
  const ground = new THREE.Color(preset.floorColor).toArray();
  const params = preset.lighting || {};
  const texture = studioEnvironmentMap({}, {...params,cards:!caseLights[preset.id]});
  const {data, width:envWidth, height:envHeight} = texture.image;
  const bearing = Math.atan2(cameraDirection.z, cameraDirection.x);
  for (let y=0; y<envHeight; y++) {
    const elevation = ((y+.5)/envHeight-.5)*Math.PI;
    const row = THREE.MathUtils.clamp(Math.floor((.5-elevation/1.7)*height),0,height-1);
    const floorMix = THREE.MathUtils.smoothstep(-elevation,.07,.92);
    for (let x=0; x<envWidth; x++) {
      const longitude = ((x+.5)/envWidth-.5)*2*Math.PI - bearing;
      const angle = Math.atan2(Math.sin(longitude), Math.cos(longitude));
      const col = THREE.MathUtils.clamp(Math.floor((.5+angle/2.25)*width),0,width-1);
      const photoWeight = (1-THREE.MathUtils.smoothstep(Math.abs(angle),.85,1.65))*(1-floorMix);
      const p = (row*width+col)*4, i=(y*envWidth+x)*4;
      const illumination = data[i];
      for (let c=0; c<3; c++) {
        const bounce = THREE.MathUtils.lerp(ambient[c],ground[c],floorMix);
        const room = (.1+.42*bounce)*(params.ambient ?? 1);
        const behind = linear[photo[p+c]]*.82;
        const base = THREE.MathUtils.lerp(room,behind,photoWeight);
        data[i+c] = base*(preset.environmentGain ?? 1) + Math.max(0,illumination-.32)*(.82+.18*ambient[c]);
      }
    }
  }
  texture.needsUpdate = true;
  return texture;
}

const yAxis = new THREE.Vector3(0,1,0);
const toRadians = THREE.MathUtils.degToRad;
let areaUniforms;
export function prepareBackdropLights(preset) {
  if(!caseLights[preset.id]?.some(light=>light.type==='AREA'))return;
  if(!areaUniforms)areaUniforms=import('./vendor/three/examples/jsm/lights/RectAreaLightUniformsLib.js')
    .then(({RectAreaLightUniformsLib})=>RectAreaLightUniformsLib.init())
    .catch(error=>{areaUniforms=null;throw error;});
  return areaUniforms;
}

function defaultLights(bounds) {
  const d=bounds.diagonal, c=new THREE.Vector3(...bounds.center);
  return [
    ['Key',-1,1.65,1.16,.95,.75,60],
    ['Fill',1,.8,.6,1.15,.9,18],
    ['Rim',.6,1.2,-1,.28,1.05,26],
  ].map(([name,x,y,z,w,h,flux])=>({type:'AREA',shape:'RECTANGLE',name,
    position:new THREE.Vector3(x,y,z).multiplyScalar(d).add(c).toArray(),target:c.toArray(),
    width_world:w*d,height_world:h*d,energy:flux*d*d}));
}

export function backdropLighting(metadata, bounds, preset) {
  if (!preset || preset.id === 'white') return metadata;
  const params = preset.lighting || {}, center=new THREE.Vector3(...bounds.center);
  const yaw = toRadians(params.yaw || 0);
  const rotatePoint = p => new THREE.Vector3(...p).sub(center).applyAxisAngle(yAxis,yaw).add(center).toArray();
  const rotateVector = p => new THREE.Vector3(...p).applyAxisAngle(yAxis,yaw).toArray();
  const layout=caseLights[preset.id], envOnly=preset.id==='welcome';
  const inputs = envOnly?[]:layout || (metadata.lights?.length ? metadata.lights : defaultLights(bounds));
  const lights = inputs.map((src,i) => {
    if(layout){
      const d=bounds.diagonal, point=p=>new THREE.Vector3(...p).applyAxisAngle(yAxis,yaw).multiplyScalar(d).add(center).toArray();
      const light={...src,position:point(src.position),target:point(src.target),energy:src.energy*d*d};
      if(src.type==='AREA'){light.width_world=src.width_world*d;light.height_world=src.height_world*d;}
      return light;
    }
    const name=src.name || '';
    const role=src.role || (/fill|辅灯/i.test(name)?'fill':/rim|back|轮廓/i.test(name)?'rim':/key|主灯/i.test(name)?'key':['key','fill','rim'][i%3]);
    const out = {...src, energy:(src.energy ?? src.power ?? src.intensity ?? 1)*(params[role] ?? 1)};
    if (src.position) out.position=rotatePoint(src.position);
    if (src.target) out.target=rotatePoint(src.target);
    for (const prop of ['direction','axis_x','axis_y']) if (src[prop]) out[prop]=rotateVector(src[prop]);
    return out;
  });
  const strength=(metadata.world?.strength ?? 1)*(params.ambient ?? 1);
  // The interactive rig provides a bounded baseline for imported scenes with
  // a dim world. Use the same environment scale for their traced reflections.
  return {...metadata, lights, physicalBackdropLights:!!layout, envOnly, floorColor:preset.floorColor, environmentColor:preset.environmentColor, groundShadow:envOnly?0:preset.groundShadow,
    world:{...metadata.world, strength,studioIntensity:Math.min(8,.46+.74*Math.max(0,strength))}};
}

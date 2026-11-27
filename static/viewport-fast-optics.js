import * as THREE from './vendor/three/build/three.module.js';
import {enclosesVolume} from './viewport-trace-scene.js';

function reflective(m) {
  return m?.isMeshStandardMaterial && ((m.metalness>.12 && m.roughness<.82) ||
    (m.clearcoat>.25 && m.clearcoatRoughness<.5));
}

// Three's transmission buffer already contains the opaque geometry behind
// the product. A curved solid has a second interface where the ray exits.
// Approximate its local radius from the surface derivatives. Project the
// outgoing ray to the actual depth of a nearby panel before sampling it.
const glassRay = `
uniform mat4 wyReceiverFromWorld[4];
uniform int wyReceiverCount;
float wyReceiverDistance(vec3 origin, vec3 direction, float fallback) {
  float nearest=1e20;
  for(int i=0;i<4;i++) {
    if(i>=wyReceiverCount) break;
    vec3 p=(wyReceiverFromWorld[i]*vec4(origin,1.0)).xyz;
    vec3 d=mat3(wyReceiverFromWorld[i])*direction;
    if(abs(d.z)<1e-6) continue;
    float t=-p.z/d.z;
    if(t>0.0 && t<nearest && all(lessThanEqual(abs(p.xy+d.xy*t),vec2(1.0)))) nearest=t;
  }
  return nearest<1e20 ? nearest : fallback;
}
vec3 wyInternalRadiance(vec3 origin, vec3 direction, float roughness) {
  vec3 radiance=vec3(0.0);
  #if defined(USE_ENVMAP) && defined(ENVMAP_TYPE_PMREM)
    radiance=textureLod(envMap,envMapRotation*direction,roughnessToMip(roughness)).rgb*envMapIntensity;
  #endif
  #if NUM_RECT_AREA_LIGHTS > 0
    vec3 originVS=(viewMatrix*vec4(origin,1.0)).xyz;
    vec3 dirVS=mat3(viewMatrix)*direction;
    float nearest=wyReceiverDistance(origin,direction,1e20);
    for(int i=0;i<NUM_RECT_AREA_LIGHTS;i++) {
      RectAreaLight lamp=rectAreaLights[i];
      vec3 facing=cross(lamp.halfWidth,lamp.halfHeight);
      float cosLight=dot(dirVS,facing);
      if(cosLight<=0.0) continue;
      float t=dot(lamp.position-originVS,facing)/cosLight;
      if(t<=0.0 || t>=nearest) continue;
      vec3 offset=originVS+t*dirVS-lamp.position;
      vec2 uv=vec2(dot(offset,lamp.halfWidth)/dot(lamp.halfWidth,lamp.halfWidth),
        dot(offset,lamp.halfHeight)/dot(lamp.halfHeight,lamp.halfHeight));
      float edge=max(abs(uv.x),abs(uv.y));
      float blur=max(.006,roughness*roughness*t/min(length(lamp.halfWidth),length(lamp.halfHeight)));
      if(edge<1.0+blur) {
        radiance=mix(radiance,lamp.color,1.0-smoothstep(1.0-blur,1.0+blur,edge));
        nearest=t;
      }
    }
  #endif
  return radiance;
}
vec3 wyVolumeRay(vec3 n, vec3 v, float thickness, float ior, float roughness, mat4 model, vec3 pos,
  out float distanceInGlass, out vec4 internal) {
  vec3 ray = getVolumeTransmissionRay(n,v,thickness,ior,model);
  internal=vec4(0.0);
  float depth = length(ray);
  distanceInGlass = depth;
  if (depth < 1e-7) return ray;
  float kx = length(dFdx(n))/max(1e-7,length(dFdx(pos)));
  float ky = length(dFdy(n))/max(1e-7,length(dFdy(pos)));
  float curvature = sqrt(kx*ky);
  if (curvature*depth < 0.28) return ray;
  float radius = 1.0/max(curvature,1e-7);
  vec3 inside = normalize(ray);
  float chord = -2.0*radius*dot(n,inside);
  if (chord <= 0.0 || chord > depth*1.8) return ray;
  vec3 exitNormal = normalize(n+inside*(chord/radius));
  vec3 outside = refract(inside,-exitNormal,ior);
  if (dot(outside,outside)<0.25) return ray;
  distanceInGlass = chord;
  vec3 travel=inside*chord;
  // One Fresnel reflection at the far interface can magnify a softbox over
  // the glass interior. Follow that branch back out of the curved solid.
  vec3 bounced=reflect(inside,exitNormal);
  float backChord=-2.0*radius*dot(exitNormal,bounced);
  vec3 backNormal=normalize(exitNormal+bounced*(backChord/radius));
  vec3 towardsLight=refract(bounced,-backNormal,ior);
  if(dot(towardsLight,towardsLight)>.25) {
    float f0=(ior-1.0)/(ior+1.0);
    float f=f0*f0+(1.0-f0*f0)*pow(1.0-clamp(dot(exitNormal,outside),0.0,1.0),5.0);
    internal=vec4(wyInternalRadiance(pos+travel+backChord*bounced,towardsLight,roughness),f);
    internal.rgb*=volumeAttenuation(backChord,attenuationColor,attenuationDistance);
  }
  return travel+outside*wyReceiverDistance(pos+travel,outside,min(depth*0.6,radius));
}`;

const localReflection = `
uniform samplerCube wyProbeA,wyProbeB;
uniform vec3 wyProbeCenterA,wyProbeCenterB;
varying vec3 wyOpticsPosition;
vec3 wyReflectedScene(vec3 studio,vec3 direction,float roughness) {
  vec3 da=wyOpticsPosition-wyProbeCenterA, db=wyOpticsPosition-wyProbeCenterB;
  // Rendering the probe once in world space keeps the same physical
  // neighbors visible as the camera orbits, including off-screen parts.
  float level=roughness*roughness*7.0;
  vec4 hit=dot(da,da)<=dot(db,db) ? textureLod(wyProbeA,direction,level) : textureLod(wyProbeB,direction,level);
  float influence=1.0-smoothstep(0.40,0.82,roughness);
  return mix(studio,hit.rgb/max(hit.a,0.001),hit.a*influence);
}`;

const preOpticsHooks=new WeakMap();

// Three's clone() omits shader callbacks. Keep the surface shader while
// allowing a probe proxy or a selected copy to get its own optical bindings.
export function cloneViewportMaterial(material) {
  const clone=material.clone(),base=preOpticsHooks.get(material) || material;
  clone.onBeforeCompile=base.onBeforeCompile;
  clone.customProgramCacheKey=base.customProgramCacheKey;
  return clone;
}

function installShader(material, state) {
  const originalCompile=material.onBeforeCompile;
  const originalKey=material.customProgramCacheKey;
  preOpticsHooks.set(material,{onBeforeCompile:originalCompile,customProgramCacheKey:originalKey});
  const inheritedKey=originalKey.call(material);
  const glass=material.transmission>0;
  const mirror=state.probeA.value && reflective(material);
  const key='-wuying-optics-'+Number(!!mirror)+Number(glass);
  material.customProgramCacheKey=()=>(originalKey===THREE.Material.prototype.customProgramCacheKey ?
    inheritedKey : originalKey.call(material))+key;
  material.onBeforeCompile=function(shader,renderer) {
    originalCompile.call(this,shader,renderer);
    if (mirror) {
      Object.assign(shader.uniforms,{
        wyProbeA:state.probeA,wyProbeB:state.probeB,
        wyProbeCenterA:state.centerA,wyProbeCenterB:state.centerB,
      });
      shader.vertexShader='varying vec3 wyOpticsPosition;\n'+shader.vertexShader.replace(
        '#include <worldpos_vertex>','#include <worldpos_vertex>\nwyOpticsPosition=worldPosition.xyz;');
      const env=THREE.ShaderChunk.envmap_physical_pars_fragment;
      const lookup='return envMapColor.rgb * envMapIntensity;';
      shader.fragmentShader=localReflection+'\n'+shader.fragmentShader.replace(
        '#include <envmap_physical_pars_fragment>',env.replace(lookup,
          'return wyReflectedScene(envMapColor.rgb * envMapIntensity,reflectVec,roughness);'));
    }
    if (glass) {
      shader.uniforms.wyVolumeDepth=state.depth;
      shader.uniforms.wyReceiverCount=state.receiverCount;
      shader.uniforms.wyReceiverFromWorld=state.receivers;
      const pars=THREE.ShaderChunk.transmission_pars_fragment.replace('vec4 getIBLVolumeRefraction(',glassRay+'\nvec4 getIBLVolumeRefraction(')
        .replace(/vec3 transmissionRay = getVolumeTransmissionRay\( n, v, thickness, (ior|iors\[ i \]), modelMatrix \);/g,
          'float distanceInGlass; vec4 internal; vec3 transmissionRay=wyVolumeRay(n,v,thickness,$1,roughness,modelMatrix,position,distanceInGlass,internal);')
        .replace('transmittedLight[ i ] = transmissionSample[ i ];',
          'transmittedLight[i]=mix(transmissionSample[i],internal[i],internal.a);')
        .replace('transmittedLight = getTransmissionSample( refractionCoords, roughness, ior );',
          'transmittedLight=getTransmissionSample(refractionCoords,roughness,ior); transmittedLight.rgb=mix(transmittedLight.rgb,internal.rgb,internal.a);')
        .replaceAll('volumeAttenuation( length( transmissionRay ),','volumeAttenuation( distanceInGlass,');
      const frag=THREE.ShaderChunk.transmission_fragment.replace('material.thickness = thickness;',
        'material.thickness = max(thickness,wyVolumeDepth);')
        .replace('normalize( cameraPosition - pos )','isOrthographic ? transformDirectionByInverseViewMatrix(vec3(0.0,0.0,1.0),viewMatrix) : normalize(cameraPosition-pos)');
      shader.fragmentShader='uniform float wyVolumeDepth;\n'+shader.fragmentShader
        .replace('#include <transmission_pars_fragment>',pars).replace('#include <transmission_fragment>',frag);
    }
  };
  material.needsUpdate=true;
}

function proxyMaterial(m) {
  if(!m) return new THREE.MeshBasicMaterial({visible:false});
  // Standard materials see the same HDR environment and area lights as the
  // main model. Remove transmission only in the cubemap to avoid six nested
  // transmission captures, including any old local-reflection shader hooks.
  const transmissive=m.transmission || 0;
  const proxy=cloneViewportMaterial(m);
  if(transmissive>0) {
    proxy.transmission=0;
    proxy.opacity*=1-.83*transmissive;
    proxy.transparent=true;
    proxy.depthWrite=false;
  }
  return proxy;
}

// The color transmission pass already draws the real opaque meshes. Cache
// up to four broad, thin receivers (e.g. a sign, wall, or tabletop) to locate
// those colors along the outgoing ray, including under camera rotation.
function transmissionReceivers(rows) {
  const found=[];
  for(const {mesh,mats} of rows) {
    const box=mesh.geometry.boundingBox;
    if(!box || mesh.isInstancedMesh || !mats.every(m=>m?.visible && !m.transparent && !m.transmission))continue;
    const size=box.getSize(new THREE.Vector3()).toArray();
    const scale=new THREE.Vector3().setFromMatrixScale(mesh.matrixWorld).toArray();
    const span=size.map((length,i)=>length*scale[i]);
    const [x,y,z]=[0,1,2].sort((a,b)=>span[b]-span[a]);
    if(span[y]<=0 || span[z]>span[y]*.15) continue;
    const center=box.getCenter(new THREE.Vector3()).toArray();
    const map=new THREE.Matrix4().makeTranslation(-center[0],-center[1],-center[2])
      .multiply(mesh.matrixWorld.clone().invert());
    const basis=new THREE.Matrix4();
    basis.elements.fill(0);
    basis.elements[x*4]=2/size[x];basis.elements[y*4+1]=2/size[y];
    basis.elements[z*4+2]=scale[z];basis.elements[15]=1;
    found.push({area:span[x]*span[y],matrix:basis.multiply(map)});
  }
  return found.sort((a,b)=>b.area-a.area).slice(0,4);
}

export class FastViewportOptics {
  constructor(view) {
    this.view=view;
    this.dirty=true;
    this.sceneChanged=true;
    this.probes=[];
    this.slots=new WeakMap();
    this.solidDepths=new WeakMap();
    this.receiverCount={value:0};
    this.receivers={value:Array.from({length:4},()=>new THREE.Matrix4())};
  }

  invalidate() { this.dirty=true;this.sceneChanged=true; }
  refreshBindings() { this.dirty=true; }

  depth(mesh,material) {
    if (material.thickness>0 || material.userData?.wuyingTrace?.thinWalled) return 0;
    const g=mesh.geometry;
    if (!this.solidDepths.has(g)) {
      // Complex undeclared volumes are resolved in the full worker; keep
      // the interactive path bounded when a CAD export has millions of faces.
      let depth=0;
      if ((g.index?.count ?? g.attributes.position.count)<=75000 && enclosesVolume(g)) {
        const s=g.boundingBox.getSize(new THREE.Vector3());
        depth=Math.min(s.x,s.y,s.z);
      }
      this.solidDepths.set(g,depth);
    }
    return this.solidDepths.get(g);
  }

  prepare(hovering=false) {
    if (!this.dirty || !this.view.model) return;
    this.dirty=false;
    const {model,bounds}=this.view;
    const rows=[], candidates=[], optics=new Map();
    model.updateWorldMatrix(true,true);
    model.traverseVisible(mesh=>{
      if (!mesh.isMesh || !mesh.geometry?.attributes.position || (mesh.isInstancedMesh && !mesh.count)) return;
      const mats=Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      if (!mats.some(m=>m?.visible)) return;
      rows.push({mesh,mats});
      const active=mats.filter(m=>m?.visible && (reflective(m) || m.transmission>0));
      if (!active.length) return;
      const g=mesh.geometry;
      if (!g.boundingSphere) g.computeBoundingSphere();
      const sphere=g.boundingSphere.clone();
      if(mesh.isInstancedMesh) {
        const instance=new THREE.Matrix4();mesh.getMatrixAt(0,instance);sphere.applyMatrix4(instance);
      }
      sphere.applyMatrix4(mesh.matrixWorld);
      for (const m of active) {
        if (!optics.has(m) || sphere.radius>optics.get(m).radius) optics.set(m,{mesh,radius:sphere.radius});
      }
      const m=active.find(reflective);
      if (m) candidates.push({mesh,reflector:m,position:sphere.center,radius:sphere.radius,
        score:sphere.radius*(.15+(m.metalness || m.clearcoat)*(1-m.roughness))});
    });
    if(this.sceneChanged && !hovering && [...optics.keys()].some(m=>m.transmission>0)) {
      const receivers=transmissionReceivers(rows);
      this.receiverCount.value=receivers.length;
      receivers.forEach(({matrix},i)=>this.receivers.value[i].copy(matrix));
    }
    if (((this.sceneChanged && !hovering) || !this.probes.length) && candidates.length) {
      candidates.sort((a,b)=>b.score-a.score);
      const first=candidates[0], second=candidates.find(row=>row!==first &&
        row.position.distanceTo(first.position)>Math.max(bounds.diagonal*.12,first.radius*.65));
      this.capture(rows,second ? [first,second] : [first]);
    }
    if(!hovering)this.sceneChanged=false;
    for (const [m,{mesh}] of optics) {
      const a=this.probes[0], b=this.probes[1] || a;
      let s=this.slots.get(m);
      if (!s) {
        s={probeA:{value:a?.target.texture},probeB:{value:b?.target.texture},
          centerA:{value:new THREE.Vector3()},centerB:{value:new THREE.Vector3()},depth:{value:0},
          receivers:this.receivers,receiverCount:this.receiverCount};
        this.slots.set(m,s);
        installShader(m,s);
      }
      if (a) {
        s.probeA.value=a.target.texture;s.probeB.value=b.target.texture;
        s.centerA.value.copy(a.position);s.centerB.value.copy(b.position);
      }
      if (m.transmission>0) s.depth.value=this.depth(mesh,m);
    }
  }

  capture(rows,centers) {
    const {renderer,bounds}=this.view, diagonal=bounds.diagonal;
    const scene=new THREE.Scene(), proxies=new Map(), mats=new Map();
    scene.environment=this.view.scene.environment;
    scene.environmentIntensity=this.view.scene.environmentIntensity;
    const lookup=m=>{
      if (!mats.has(m)) mats.set(m,proxyMaterial(m));
      return mats.get(m);
    };
    // Camera-independent probes use the actual studio rig. Disable shadow
    // maps here: the studio floor already has a baked contact/penumbra mask.
    for(const light of this.view.scene.children) {
      if(light===this.view.viewFill || !light.visible) continue;
      if(light.isHemisphereLight || light.isRectAreaLight || light.isPointLight) scene.add(light.clone());
      else if(light.isDirectionalLight || light.isSpotLight) {
        const copy=light.clone();
        copy.castShadow=false;scene.add(copy,copy.target);
      }
    }
    for (const {mesh,mats:source} of rows) {
      const materials=Array.isArray(mesh.material) ? source.map(lookup) : lookup(source[0]);
      let proxy;
      if (mesh.isInstancedMesh) {
        proxy=new THREE.InstancedMesh(mesh.geometry,materials,0);
        proxy.instanceMatrix=mesh.instanceMatrix;
        proxy.instanceColor=mesh.instanceColor;proxy.count=mesh.count;
      } else proxy=new THREE.Mesh(mesh.geometry,materials);
      proxy.matrixAutoUpdate=false;proxy.matrix.copy(mesh.matrixWorld);
      scene.add(proxy);proxies.set(mesh,proxy);
    }
    const floorColor=this.view.studioMetadata?.floorColor;
    const floorMaterial=new THREE.MeshStandardMaterial({color:floorColor || new THREE.Color().setRGB(.78,.78,.78),roughness:1});
    const floor=new THREE.Mesh(new THREE.PlaneGeometry(diagonal*20,diagonal*20),floorMaterial);
    floor.rotation.x=-Math.PI/2;
    floor.position.set(bounds.center[0],bounds.min[1]-diagonal*.0015,bounds.center[2]);
    if(this.view.studioMetadata?.groundShadow!==0)scene.add(floor);
    if(this.view.studioGround?.result) scene.add(this.view.studioGround.mesh.clone());
    const clear=renderer.getClearColor(new THREE.Color()),alpha=renderer.getClearAlpha();
    const updateShadows=renderer.shadowMap.needsUpdate;
    try {
      renderer.setClearColor(0,0);
      for (const [index,c] of centers.entries()) {
        let probe=this.probes[index];
        if (!probe) {
          const type=renderer.extensions.has('EXT_color_buffer_float') ? THREE.HalfFloatType : THREE.UnsignedByteType;
          const target=new THREE.WebGLCubeRenderTarget(128,{type,
            generateMipmaps:true,minFilter:THREE.LinearMipmapLinearFilter,magFilter:THREE.LinearFilter});
          probe=this.probes[index]={target,position:new THREE.Vector3()};
        }
        const camera=new THREE.CubeCamera(Math.max(1e-6,diagonal*.0004),diagonal*30,probe.target);
        camera.position.copy(c.position);probe.position.copy(c.position);
        // Blender can export painted trim, a label and its metal housing
        // under the same object name. Exclude only the reflector itself.
        const receiver=proxies.get(c.mesh), saved=receiver.material;
        const source=Array.isArray(c.mesh.material)?c.mesh.material:[c.mesh.material];
        const mask=c.mesh.isInstancedMesh ? lookup(c.reflector).clone() : lookup(null);
        // From inside a solid instanced reflector its front faces are culled;
        // the other instances remain visible in its reflection.
        if(c.mesh.isInstancedMesh)mask.side=THREE.FrontSide;
        const filtered=source.map((m,i)=>m===c.reflector?mask:(Array.isArray(saved)?saved[i]:saved));
        receiver.material=Array.isArray(saved)?filtered:filtered[0];
        try { camera.update(renderer,scene); }
        finally {
          receiver.material=saved;
          if(c.mesh.isInstancedMesh)mask.dispose();
        }
      }
      for (const obsolete of this.probes.splice(centers.length)) obsolete.target.dispose();
    } finally {
      renderer.setClearColor(clear,alpha);
      renderer.shadowMap.needsUpdate=updateShadows;
      for (const material of mats.values()) material.dispose();
      floor.geometry.dispose();floorMaterial.dispose();
    }
  }

  dispose() { for (const p of this.probes) p.target.dispose();this.probes.length=0; }
}

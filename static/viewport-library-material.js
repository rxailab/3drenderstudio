import * as THREE from './vendor/three/build/three.module.js';
import {woodParameters,wireWoodShader} from './viewport-wood-material.js';

// Tileable, lightweight relief for procedural presets. The output channels
// are generated separately so a source image always takes precedence.
function proceduralMaps(spec,range,material,textures,allowColor,allowRoughness) {
  if(!spec || spec.type==='toon')return;
  const size=128,relief=new Uint8Array(size*size*4);
  const colors=allowColor && spec.colors?.length>=2 ? new Uint8Array(relief.length) : null;
  const rough=allowRoughness && range ? new Uint8Array(relief.length) : null;
  const hash=(x,y)=>{
    const n=Math.imul(x+1,91771)^Math.imul(y+7,62731);
    return ((Math.imul(n^(n>>>13),1274126177)>>>0)%65536)/65535;
  };
  const tau=2*Math.PI;
  for(let y=0;y<size;y++)for(let x=0;x<size;x++){
    const u=x/size,v=y/size,noise=hash(x,y),i=4*(y*size+x);
    let h=noise;
    switch(spec.type){
      case 'brush': h=.8*hash(0,y)+.2*noise;break;
      case 'weave': h=.5+.23*Math.sin(u*16*tau)+.23*Math.sin(v*16*tau)*Math.cos(u*8*tau);break;
      case 'grid': h=(x%32<2 || y%16<2)?.15:.82+.1*noise;break;
      case 'marble': h=Math.pow(.5+.5*Math.cos(3*tau*u+1.8*Math.sin(v*tau)),6);break;
      case 'cells': case 'leather': {
        let nearest=2,second=2;
        const gx=Math.floor(x/16),gy=Math.floor(y/16);
        for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){
          const nx=gx+dx,ny=gy+dy,k=(nx+8)%8,l=(ny+8)%8;
          const dist=Math.hypot(x/16-nx-hash(k,l),y/16-ny-hash(l+19,k));
          if(dist<nearest){second=nearest;nearest=dist;}else second=Math.min(second,dist);
        }
        h=Math.min(1,(second-nearest)*3);break;
      }
    }
    const val=Math.round(h*255);
    relief.set([val,val,val,255],i);
    if(rough){
      const r=Math.round(255*(range[0]+h*(range[1]-range[0])));rough.set([r,r,r,255],i);
    }
    if(colors){
      for(let c=0;c<3;c++)colors[i+c]=Math.round(255*Math.min(1,Math.max(0,spec.colors[0][c]*(1-h)+spec.colors[1][c]*h)));
      colors[i+3]=255;
    }
  }
  function map(bytes){
    const texture=new THREE.DataTexture(bytes,size,size,THREE.RGBAFormat);
    texture.wrapS=texture.wrapT=THREE.RepeatWrapping;texture.repeat.set(3,3);
    texture.rotation=THREE.MathUtils.degToRad(spec.rotation || 0);
    texture.generateMipmaps=true;texture.minFilter=THREE.LinearMipmapLinearFilter;texture.magFilter=THREE.LinearFilter;
    texture.needsUpdate=true;textures.push(texture);return texture;
  }
  material.bumpMap=map(relief);material.bumpScale=spec.type==='brush'?.00015:.0004;
  if(rough){material.roughnessMap=map(rough);material.roughness=1;}
  if(colors){material.map=map(colors);material.color.setRGB(1,1,1);}
}

// The catalog and the offline renderer use the same linear PBR description.
// The preview owns all of these textures; none belong to the imported GLB.
function makeSurface(item,onChange,overrides,generatedScale,wood) {
  const pbr=item.pbr;
  const color=overrides.color?new THREE.Color(overrides.color):new THREE.Color().fromArray(pbr.color);
  const material=new THREE.MeshPhysicalMaterial({
    name:item.name,color,metalness:overrides.metallic ?? pbr.metallic,roughness:overrides.roughness ?? pbr.roughness,
    transmission:overrides.transmission ?? pbr.transmission,ior:pbr.ior,clearcoat:overrides.coat ?? pbr.coat,clearcoatRoughness:.03,
    opacity:pbr.alpha ?? 1,transparent:(pbr.alpha ?? 1)<1 || !!pbr.textures?.alpha,
    sheen:pbr.sheen ?? 0,sheenColor:color,anisotropy:pbr.anisotropy ?? 0,
    emissive:color,emissiveIntensity:pbr.emission_strength ?? 0,side:THREE.DoubleSide
  });
  material.userData.wuyingTrace={
    thinWalled:item.category==='Multi-Layer Optics' || (item.category==='Architectural' && item.subcategory==='Window'),
    roughnessRange:null,wood
  };
  const textures=[],loader=new THREE.TextureLoader();
  const channels={base_color:'map',roughness:'roughnessMap',metallic:'metalnessMap',normal:'normalMap',height:'bumpMap',alpha:'alphaMap'};
  if(!wood)proceduralMaps(pbr.procedural,pbr.roughness_range,material,textures,
    !pbr.textures?.base_color && !overrides.color,!pbr.textures?.roughness && overrides.roughness===undefined);
  material.onBeforeCompile=shader=>{
    // STL and some OBJ surfaces have no UVs. Box-project only these catalog
    // shaders; keep the original geometry and its other materials untouched.
    if(generatedScale){
      shader.uniforms.wyGeneratedScale={value:generatedScale};
      const uv=THREE.ShaderChunk.uv_vertex.replace(/\b(?:uv|MAP_UV|ALPHAMAP_UV|BUMPMAP_UV|NORMALMAP_UV|METALNESSMAP_UV|ROUGHNESSMAP_UV)\b/g,'wyUV');
      shader.vertexShader='uniform float wyGeneratedScale;\n'+shader.vertexShader.replace('#include <uv_vertex>',`
        vec3 wyPos = (modelMatrix * vec4(position, 1.0)).xyz * wyGeneratedScale;
        vec3 wyAxis = abs(normalize(mat3(modelMatrix) * normal));
        vec2 wyUV = wyAxis.x > wyAxis.y && wyAxis.x > wyAxis.z ? wyPos.zy : (wyAxis.y > wyAxis.z ? wyPos.xz : wyPos.xy);
        ${uv}`);
    }
    if(pbr.roughness_range && pbr.textures?.roughness && material.roughnessMap){
      shader.uniforms.wyRoughRange={value:new THREE.Vector2(...pbr.roughness_range)};
      shader.fragmentShader='uniform vec2 wyRoughRange;\n'+shader.fragmentShader.replace('#include <roughnessmap_fragment>',
        '#include <roughnessmap_fragment>\nroughnessFactor = mix(wyRoughRange.x, wyRoughRange.y, roughnessFactor);');
    }
    if(wood)wireWoodShader(shader,wood);
  };
  material.customProgramCacheKey=()=>`catalog-${generatedScale?'projected':'uv'}-${!!(pbr.roughness_range && pbr.textures?.roughness && material.roughnessMap)}-${!!wood}`;
  let disposed=false;
  for(const [channel,src] of Object.entries(pbr.textures || {})){
    if(!channels[channel] || typeof src!=='string' || Object.hasOwn(overrides,channel==='base_color'?'color':channel))continue;
    const texture=loader.load(src,()=>{
      if(disposed)return;
      material[channels[channel]]=texture;
      if(channel==='base_color')material.color.fromArray(pbr.texture_tint ?? [1,1,1]);
      if(channel==='roughness'){
        material.roughness=1;
        material.userData.wuyingTrace.roughnessRange=pbr.roughness_range || null;
      }
      if(channel==='metallic')material.metalness=1;
      material.needsUpdate=true;onChange();
    },undefined,()=>{ /* Keep the scalar fallback when the image is unavailable. */ });
    texture.flipY=false;texture.wrapS=texture.wrapT=THREE.RepeatWrapping;
    texture.colorSpace=channel==='base_color'?THREE.SRGBColorSpace:THREE.NoColorSpace;
    textures.push(texture);
  }
  return {material,dispose(){disposed=true;for(const texture of textures)texture.dispose();material.dispose();}};
}

export function makeLibraryPreviewMaterial(item,onChange,diagonal,overrides={}) {
  const variants=new Map(),woodVariants=new Map();
  return {
    forGeometry(geometry,space){
      const scale=geometry.getAttribute('uv')?0:1/Math.max(diagonal,1e-6);
      if(item.pbr.procedural?.type==='wood'){
        const key=space || geometry;
        if(!woodVariants.has(key))woodVariants.set(key,new Map());
        const byScale=woodVariants.get(key);
        if(!byScale.has(scale))byScale.set(scale,makeSurface(item,onChange,overrides,scale,woodParameters(item.pbr,overrides,geometry,space)));
        return byScale.get(scale).material;
      }
      if(!variants.has(scale))variants.set(scale,makeSurface(item,onChange,overrides,scale,null));
      return variants.get(scale).material;
    },
    dispose(){
      for(const group of [variants,...woodVariants.values()])for(const surface of group.values())surface.dispose();
      variants.clear();woodVariants.clear();
    }
  };
}

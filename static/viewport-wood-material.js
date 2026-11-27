import {Box3,Matrix4,Vector3,Vector4} from './vendor/three/build/three.module.js';
import {woodWaveGLSL} from './viewport-wood-wave.js';

// glTF stores (x,z,-y) for Blender's (x,y,z). Generated coordinates belong to
// the Blender object, including all primitives that share its texture space.
export function localToGenerated({center,half_size}) {
  const [x,y,z]=half_size.map(value=>.5/(value || 1));
  return new Matrix4().set(
    x,0,0,.5-center[0]*x,
    0,0,-y,.5-center[1]*y,
    0,z,0,.5-center[2]*z,
    0,0,0,1);
}

function spaceFromBox(box) {
  const center=box.getCenter(new Vector3()),half=box.getSize(new Vector3()).multiplyScalar(.5);
  return {center:[center.x,-center.z,center.y],half_size:[half.x,half.z,half.y]};
}

export function assignGeneratedSpaces(model,spaces={}) {
  const boxes=new Map(),meshes=[];
  model.traverse(node=>{
    if(!node.isMesh)return;
    meshes.push(node);
    const name=node.userData.productObjectName;
    if(Object.hasOwn(spaces,name))return;
    if(!node.geometry.boundingBox)node.geometry.computeBoundingBox();
    if(!boxes.has(name))boxes.set(name,new Box3());
    boxes.get(name).union(node.geometry.boundingBox);
  });
  const fallback=new Map([...boxes].map(([name,box])=>[name,spaceFromBox(box)]));
  for(const node of meshes){
    const name=node.userData.productObjectName;
    node.userData.generatedSpace=Object.hasOwn(spaces,name)?spaces[name]:fallback.get(name);
  }
}

export function woodParameters(pbr,overrides,geometry,space) {
  if(!space){
    if(!geometry.boundingBox)geometry.computeBoundingBox();
    space=spaceFromBox(geometry.boundingBox);
  }
  const spec=pbr.procedural;
  return {
    scale:.5*Math.min(220,Math.max(2,.18/Math.max(.0001,spec.size ?? .01))),
    rotation:(spec.rotation || 0)*Math.PI/180,
    colors:!pbr.textures?.base_color && !Object.hasOwn(overrides,'color')?spec.colors ?? null:null,
    range:!pbr.textures?.roughness && !Object.hasOwn(overrides,'roughness')?pbr.roughness_range ?? null:null,
    bumpDistance:pbr.textures?.height?0:.0006,
    localToGenerated:localToGenerated(space).toArray()
  };
}

export function wireWoodShader(shader,wood) {
  const {colors,range}=wood;
  Object.assign(shader.uniforms,{
    wyLocalToGenerated:{value:new Matrix4().fromArray(wood.localToGenerated)},
    wyWoodParams:{value:new Vector4(wood.scale,wood.rotation,range?.[0] ?? -1,range?.[1] ?? -1)},
    wyWoodLow:{value:new Vector4(...(colors?.[0] ?? [0,0,0]),Number(!!colors))},
    wyWoodHigh:{value:new Vector4(...(colors?.[1] ?? [0,0,0]),wood.bumpDistance)}
  });
  shader.vertexShader='varying vec3 wyGenerated;\nuniform mat4 wyLocalToGenerated;\n'+shader.vertexShader
    .replace('#include <begin_vertex>','#include <begin_vertex>\nwyGenerated=(wyLocalToGenerated*vec4(position,1.)).xyz;');
  shader.fragmentShader=`varying vec3 wyGenerated;
    uniform vec4 wyWoodParams,wyWoodLow,wyWoodHigh;
    ${woodWaveGLSL}\n`+shader.fragmentShader
    .replace('#include <map_fragment>',`#include <map_fragment>
      vec4 wyField=wyWoodWaveGradient(wyGenerated,wyWoodParams.x,wyWoodParams.y);
      if(wyWoodLow.w>.5)diffuseColor.rgb=mix(wyWoodLow.rgb,wyWoodHigh.rgb,wyField.w);`)
    .replace('#include <roughnessmap_fragment>',`#include <roughnessmap_fragment>
      if(wyWoodParams.z>=0.)roughnessFactor=mix(wyWoodParams.z,wyWoodParams.w,wyField.w);`)
    .replace('#include <normal_fragment_maps>',`#include <normal_fragment_maps>
      vec3 wyDx=dFdx(-vViewPosition),wyDy=dFdy(-vViewPosition);
      vec3 wyRx=cross(wyDy,normal),wyRy=cross(normal,wyDx);
      float wyDet=dot(wyDx,wyRx);
      if(wyWoodHigh.w>0. && abs(wyDet)>1e-20){
        vec3 wyTangentGradient=(wyRx*dot(wyField.xyz,dFdx(wyGenerated))+wyRy*dot(wyField.xyz,dFdy(wyGenerated)))/wyDet;
        normal=normalize(mix(normal,normalize(normal-faceDirection*wyWoodHigh.w*wyTangentGradient),.2));
      }`);
}

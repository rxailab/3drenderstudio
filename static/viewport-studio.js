import * as THREE from './vendor/three/build/three.module.js';
import { StudioGround } from './viewport-studio-ground.js';
import { addStudioLight } from './viewport-light.js';

// A large key, a broad dimmer fill and a narrow rim. The diffuse sides of
// the cards roll off near their borders like fabric softboxes in a real room.
const cards = [
  [-1,1.55,1.05, .43,.55,2.35],
  [.95,.78,.6, .58,.48,.83],
  [.7,1.04,-1.15, .16,.48,1.85],
].map(([x,y,z,width,height,power])=>{
  const n=new THREE.Vector3(x,y,z).normalize();
  const u=new THREE.Vector3(n.z,0,-n.x).normalize();
  return {n,u,v:n.clone().cross(u),width,height,power};
});

export function studioEnvironmentMap(world={},lighting={}) {
  const width=256,height=128,pixels=new Float32Array(width*height*4);
  const dir=new THREE.Vector3(), color=world.color || [1,1,1];
  const yaw=THREE.MathUtils.degToRad(lighting.yaw || 0);
  const powers=[lighting.key ?? 1,lighting.fill ?? 1,lighting.rim ?? 1];
  const cardCount=lighting.cards===false?0:cards.length;
  for(let y=0;y<height;y++){
    const lat=((y+.5)/height-.5)*Math.PI, cosLat=Math.cos(lat);
    for(let x=0;x<width;x++){
      const lon=((x+.5)/width-.5)*2*Math.PI+yaw;
      dir.set(cosLat*Math.cos(lon),Math.sin(lat),cosLat*Math.sin(lon));
      // Quiet dark side walls separate the highlights on polished metals.
      const sky=Math.max(0,dir.y), floor=Math.max(0,-dir.y);
      let light=.19+.35*sky*sky+.43*floor;
      for(let k=0;k<cardCount;k++){
        const card=cards[k];
        const front=dir.dot(card.n);
        if(front<=0)continue;
        const u=dir.dot(card.u)/(front*card.width),v=dir.dot(card.v)/(front*card.height);
        const edge=Math.max(Math.abs(u),Math.abs(v));
        if(edge<1.14)light+=powers[k]*card.power*(1-THREE.MathUtils.smoothstep(edge,.82,1.14))*(1.0-.19*Math.min(1,u*u+v*v));
      }
      const i=(y*width+x)*4;
      for(let c=0;c<3;c++)pixels[i+c]=light*color[c];
      pixels[i+3]=1;
    }
  }
  const texture=new THREE.DataTexture(pixels,width,height,THREE.RGBAFormat,THREE.FloatType);
  texture.mapping=THREE.EquirectangularReflectionMapping;texture.colorSpace=THREE.LinearSRGBColorSpace;
  texture.minFilter=texture.magFilter=THREE.LinearFilter;texture.needsUpdate=true;
  return texture;
}

export function buildStudioLighting(view,metadata) {
  const b=view.bounds,d=b.diagonal,c=new THREE.Vector3(...b.center);
  const world=Number.isFinite(metadata.world?.strength)?Math.max(0,metadata.world.strength):1;
  view.scene.environmentIntensity=Math.min(8,.46+world*.74);
  view.renderer.toneMappingExposure=2**Math.max(-10,Math.min(10,Number(metadata.exposure)||0));
  const sky=metadata.environmentColor?new THREE.Color(metadata.environmentColor).lerp(new THREE.Color(0xffffff),.67):0xffffff;
  const bounce=metadata.floorColor?new THREE.Color(metadata.floorColor).lerp(new THREE.Color(0xc7c5c2),.32):0xc7c5c2;
  // The teaching rigs supply their own fill and HDR bounce. A camera-following
  // light would put a moving specular hotspot on the interactive product.
  const calibrated=metadata.physicalBackdropLights || metadata.envOnly;
  if(!calibrated)view.scene.add(new THREE.HemisphereLight(sky,bounce,Math.min(8,.74+world*.42)));
  view.viewFill=new THREE.DirectionalLight(0xfffaf4,Math.min(1.1,.24+world*.20));
  view.viewFill.visible=!calibrated;
  view.scene.add(view.viewFill,view.viewFill.target);
  const candidates=(metadata.lights || []).filter(s=>Array.isArray(s.position)&&s.position.length===3).slice(0,6);
  const sources=candidates.length || metadata.envOnly?candidates:[
    {name:'Key',position:[c.x-d,c.y+d*1.65,c.z+d*1.16],energy:2.0},
    {name:'Fill',position:[c.x+d,c.y+d*.8,c.z+d*.6],energy:.8},
    {name:'Rim',position:[c.x+d*.6,c.y+d*1.2,c.z-d],energy:.9},
  ];
  const lights=sources.map(source=>{
    if(metadata.physicalBackdropLights){
      const light=addStudioLight(view.scene,source,b);
      return {light,source};
    }
    const color=Array.isArray(source.color)?new THREE.Color().fromArray(source.color):0xffffff;
    const power=Math.max(0,Number(source.energy ?? source.power ?? source.intensity)||0);
    const position=new THREE.Vector3(...source.position);
    const distance2=position.distanceToSquared(c);
    const intensity=!candidates.length?power:source.type==='SUN'?power*2:power*.135/Math.max(distance2,d*d*.04);
    const light=new THREE.DirectionalLight(color,Math.min(25,intensity));
    light.position.copy(position);light.target.position.fromArray(source.target || b.center);
    if(!source.target && source.direction){
      const distance=source.type==='AREA'?Math.max(d*.05,Math.sqrt(distance2)):d;
      light.target.position.copy(position).addScaledVector(new THREE.Vector3(...source.direction).normalize(),distance);
    }
    view.scene.add(light,light.target);
    return {light,source};
  });
  const active=lights.filter(({light,source})=>light?.intensity>0 && light.position.y>(source.target?.[1] ?? light.target?.position.y ?? c.y));
  const key=active.find(({source})=>source.role==='key' || /key|主灯/i.test(source.name || '')) ||
    active.sort((a,b)=>b.light.intensity-a.light.intensity)[0];
  if(key?.light.shadow){
    const shadow=key.light.shadow;key.light.castShadow=true;shadow.mapSize.set(1024,1024);
    Object.assign(shadow.camera,{near:Math.max(d*.001,.00001),far:d*30});
    if(shadow.camera.isOrthographicCamera)Object.assign(shadow.camera,{left:-d,right:d,top:d,bottom:-d});
    shadow.bias=-.0001;shadow.normalBias=d*.0008;shadow.radius=2.4;
  }
  // This cached projection already integrates the penumbra. The jitter pass
  // can refine camera pixels without submitting additional shadow maps.
  if(metadata.groundShadow!==0)view.studioGround=new StudioGround(view,key,world);
  if(view.renderer.shadowMap)view.renderer.shadowMap.needsUpdate=true;
}

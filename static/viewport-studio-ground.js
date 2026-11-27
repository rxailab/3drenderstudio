import * as THREE from './vendor/three/build/three.module.js';

const SIZE=512;

// The camera projects geometry onto the y-up ground along the key light.
// Its depth increases with height above the floor, so each texel sees the
// closest blocker to the floor, including the actual feet of irregular models.
function shadowProjection(bounds,slope){
  const [x0,floor,z0]=bounds.min,[x1,top,z1]=bounds.max;
  const height=top-floor,pad=bounds.diagonal*.22;
  const left=x0+Math.min(0,-slope.x*height)-pad, right=x1+Math.max(0,-slope.x*height)+pad;
  const back=z0+Math.min(0,-slope.z*height)-pad, front=z1+Math.max(0,-slope.z*height)+pad;
  const width=right-left,depth=front-back;
  const near=floor-bounds.diagonal*.001,range=height+bounds.diagonal*.002;
  const camera=new THREE.OrthographicCamera();
  camera.projectionMatrix.set(
    2/width,-2*slope.x/width,0,(2*slope.x*floor-left-right)/width,
    0,-2*slope.z/depth,2/depth,(2*slope.z*floor-front-back)/depth,
    0,2/range,0,-1-2*near/range,
    0,0,0,1
  );
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
  return {camera,width,depth,cx:(left+right)*.5,cz:(front+back)*.5,range};
}

function captureMaterial(source,range){
  const cutout=source.alphaTest>0 || source.transparent;
  const material=new THREE.MeshDepthMaterial({
    depthPacking:THREE.BasicDepthPacking,side:THREE.DoubleSide,
    map:cutout?source.map:null,alphaMap:cutout?source.alphaMap:null,
    alphaTest:source.alphaTest||0,opacity:source.opacity,visible:source.visible,
  });
  const density=Math.max(0,Math.min(1,1-.78*(source.transmission || 0)));
  material.onBeforeCompile=shader=>{
    shader.uniforms.groundDensity={value:density};shader.uniforms.groundRange={value:range};
    shader.fragmentShader='uniform float groundDensity,groundRange;\n'+shader.fragmentShader.replace(
      'gl_FragColor = vec4( vec3( 1.0 - fragCoordZ ), opacity );',
      `float h=max(0.0,fragCoordZ*groundRange-0.001);
       float w=clamp(diffuseColor.a*groundDensity,0.0,1.0);
       if(w<0.01)discard;
       gl_FragColor=vec4(w,w*exp(-h/0.105),w*exp(-h/0.021),1.0);`);
  };
  return material;
}

function filterPass(radius,proj,bounds,direct,ambient){
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute([-1,-1,0,3,-1,0,-1,3,0],3));
  const span=new THREE.Vector2(proj.width,proj.depth);
  const material=new THREE.RawShaderMaterial({
    glslVersion:THREE.GLSL3,depthTest:false,depthWrite:false,blending:THREE.NoBlending,
    uniforms:{mask:{value:null},span:{value:span},radius:{value:radius},
      diagonal:{value:bounds.diagonal},strength:{value:new THREE.Vector2(direct,ambient)}},
    vertexShader:`precision highp float;
      in vec3 position;out vec2 sampleUV;
      void main(){sampleUV=position.xy*.5+.5;gl_Position=vec4(position,1.);}`,
    fragmentShader:`precision highp float;
      uniform sampler2D mask;uniform vec2 span,strength;
      uniform float radius,diagonal;in vec2 sampleUV;out vec4 outColor;
      vec3 softMask(float width){
        vec2 spread=width/span;
        float lod=clamp(log2(max(1.,min(spread.x,spread.y)*${SIZE.toFixed(1)})),0.,8.);
        vec3 sum=textureLod(mask,sampleUV,lod).rgb*.24;
        // Filter several overlapping mip footprints so even a long cast
        // shadow has a smooth falloff in world space on a white sweep.
        for(int i=0;i<8;i++){
          float a=(float(i)+.5)*.78539816;
          sum+=.095*textureLod(mask,sampleUV+vec2(cos(a),sin(a))*spread*.72,lod).rgb;
        }
        return sum;
      }
      void main(){
        float castShadow=softMask(radius).r, nearBlock=softMask(radius*.32).g;
        float contact=softMask(diagonal*.0045).b;
        float shade=strength.x*(.21*castShadow+.14*nearBlock)+strength.y*.22*contact;
        outColor=vec4(vec3(clamp(shade,0.,.75)),1.);
      }`,
  });
  const quad=new THREE.Mesh(geometry,material);quad.frustumCulled=false;
  return quad;
}

export class StudioGround{
  constructor(view,key,world){
    this.view=view;
    const b=view.bounds,d=b.diagonal;
    const target=key?.light.target?.position || new THREE.Vector3(...(key?.source.target || b.center));
    const aim=key?key.light.position.clone().sub(target):new THREE.Vector3(0,1,0);
    const distance=aim.length();aim.normalize();
    const slope=new THREE.Vector3(aim.x,0,aim.z).divideScalar(Math.max(.16,aim.y));
    const proj=this.projection=shadowProjection(b,slope);
    const source=key?.source;
    const width=source?.width_world ?? source?.size ?? (source?.type==='SPOT'?Math.max(.015*d,(source.shadow_soft_size || 0)*2):d*.65);
    const height=source?.height_world ?? source?.size_y ?? width;
    const angle=source?.type==='SUN' ? .025 : Math.sqrt(width*height)/Math.max(d*.2,distance);
    const radius=d*THREE.MathUtils.clamp(angle*.054,.006,.092);
    const shadow=new THREE.ShadowMaterial({color:0x262a31,opacity:1,depthWrite:false,toneMapped:false});
    this.mesh=new THREE.Mesh(new THREE.PlaneGeometry(proj.width,proj.depth),shadow);
    this.mesh.name='Studio ground';this.mesh.rotation.x=-Math.PI*.5;
    this.mesh.position.set(proj.cx,b.min[1]-d*.0005,proj.cz);this.mesh.receiveShadow=true;
    view.scene.add(this.mesh);
    if(!view.renderer.isWebGLRenderer || !view.model){shadow.opacity=.24;return;}
    const direct=key?Math.min(1,key.light.intensity/(.2+.3*world)):0;
    const ambient=Math.min(1,.58+world*.7);
    this.filter=filterPass(radius,proj,b,direct,ambient);
    this.mask=new THREE.WebGLRenderTarget(SIZE,SIZE,{depthBuffer:true,generateMipmaps:true,
      minFilter:THREE.LinearMipmapLinearFilter,magFilter:THREE.LinearFilter});
    this.result=new THREE.WebGLRenderTarget(SIZE,SIZE,{depthBuffer:false,minFilter:THREE.LinearFilter,magFilter:THREE.LinearFilter});
    this.filter.material.uniforms.mask.value=this.mask.texture;
    shadow.onBeforeCompile=shader=>{
      shader.uniforms.studioGroundMask={value:this.result.texture};
      shader.vertexShader='varying vec2 groundUV;\n'+shader.vertexShader.replace('void main() {','void main() { groundUV=vec2(uv.x,1.-uv.y);');
      shader.fragmentShader='varying vec2 groundUV;uniform sampler2D studioGroundMask;\n'+shader.fragmentShader.replace(
        'opacity * ( 1.0 - getShadowMask() )','opacity * texture2D(studioGroundMask,groundUV).r');
    };
    this.bake();
  }

  bake(){
    if(!this.mask || this.view.disposed)return;
    const view=this.view,renderer=view.renderer,model=view.model;
    const previous=renderer.getRenderTarget(),clear=renderer.getClearColor(new THREE.Color()),alpha=renderer.getClearAlpha();
    const shadows=renderer.shadowMap.enabled,autoClear=renderer.autoClear;
    const restore=[],materials=new Map();
    const capture=m=>{
      if(!materials.has(m))materials.set(m,captureMaterial(m,this.projection.range/view.bounds.diagonal));
      return materials.get(m);
    };
    try{
      model.traverseVisible(node=>{
        if(!node.isMesh)return;
        restore.push([node,node.material]);
        node.material=Array.isArray(node.material)?node.material.map(capture):capture(node.material);
      });
      renderer.autoClear=true;renderer.shadowMap.enabled=false;renderer.setClearColor(0,0);
      renderer.setRenderTarget(this.mask);renderer.render(model,this.projection.camera);
      renderer.setRenderTarget(this.result);renderer.render(this.filter,this.projection.camera);
    }finally{
      for(const [node,original] of restore)node.material=original;
      for(const material of materials.values())material.dispose();
      renderer.setRenderTarget(previous);renderer.setClearColor(clear,alpha);
      renderer.autoClear=autoClear;renderer.shadowMap.enabled=shadows;
    }
  }

  invalidate(){
    if(!this.mask || this.refresh)return;
    this.refresh=requestAnimationFrame(()=>{
      this.refresh=0;
      if(this.view.disposed)return;
      this.bake();this.view.requestDraw();
    });
  }

  dispose(){
    if(this.refresh)cancelAnimationFrame(this.refresh);
    this.mask?.dispose();this.result?.dispose();
    this.filter?.material.dispose();this.filter?.geometry.dispose();
  }
}

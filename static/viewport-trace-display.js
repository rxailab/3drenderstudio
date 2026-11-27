import * as THREE from './vendor/three/build/three.module.js';

// Filter the floating-point accumulation before the display transform. A
// structure tensor over the patch distinguishes lines and reflection edges
// from random differences between the center and a few individual neighbors.
export function configureTraceOutput(material) {
  material.uniforms.traceSamples={value:0};
  material.fragmentShader=`uniform sampler2D map;
    uniform float traceSamples;
    varying vec2 vUv;
    vec3 rangeGuide(vec3 radiance) {
      return log2(vec3(1.0)+max(radiance,vec3(0.0)));
    }
    float squaredLength(vec3 v) { return dot(v,v); }
    vec3 filterRadiance(ivec2 p,ivec2 last,vec3 color,float converged) {
      vec3 guide=rangeGuide(color);
      vec4 total=vec4(0.0), horizontal=vec4(0.0), vertical=vec4(0.0);
      vec4 diagonalUp=vec4(0.0), diagonalDown=vec4(0.0);
      float xx=0.0, yy=0.0, xy=0.0, diagonalError=0.0;
      vec3 previousRow[5];
      for(int y=-2;y<=2;y++) {
        vec3 upperLeft=guide, left=guide;
        for(int x=-2;x<=2;x++) {
          ivec2 offset=ivec2(x,y);
          vec4 tap=texelFetch(map,clamp(p+offset,ivec2(0),last),0);
          float coverage=step(.98,tap.a);
          vec3 q=mix(guide,rangeGuide(tap.rgb),coverage);
          float weight=coverage*exp(-.23*float(x*x+y*y)-6.0*squaredLength(q-guide));
          vec4 weighted=weight*vec4(tap.rgb,1.0);
          total+=weighted;
          if(y==0) horizontal+=weighted;
          if(x==0) vertical+=weighted;
          if(x==y) diagonalUp+=weighted;
          if(x==-y) diagonalDown+=weighted;

          vec3 upper=guide;
          if(y>-2) upper=previousRow[x+2];
          if(x>-2 && y>-2) {
            // Use the four sides of each cell. Centered differences alone
            // would vanish on single-pixel, alternating stripes.
            vec3 dx0=upper-upperLeft, dx1=q-left;
            vec3 dy0=left-upperLeft, dy1=q-upper;
            xx+=.5*(squaredLength(dx0)+squaredLength(dx1));
            yy+=.5*(squaredLength(dy0)+squaredLength(dy1));
            xy+=.25*dot(dx0+dx1,dy0+dy1);
            diagonalError+=squaredLength(q-upperLeft)+squaredLength(upper-left);
          }
          previousRow[x+2]=q;
          upperLeft=upper;
          left=q;
        }
      }
      float energy=max(xx+yy,1e-8);
      float anisotropy=length(vec2(xx-yy,2.0*xy))/energy;
      float edge=smoothstep(.35,.65,anisotropy);
      vec4 along=xx<yy?horizontal:vertical;
      if(.5*energy-abs(xy)<min(xx,yy)) along=xy<0.0?diagonalUp:diagonalDown;
      vec3 lineColor=mix(along.rgb/along.a,color,.55);
      vec3 result=mix(total.rgb/total.a,lineColor,edge*converged);
      // A two-dimensional alternating texture has equal gradient energies.
      // Matching diagonals in all sixteen cells distinguish it from noise.
      float checker=1.0-smoothstep(.05,.30,(diagonalError+1e-8)/energy);
      return mix(result,color,checker*converged);
    }
    void main() {
      ivec2 size=textureSize(map,0);
      ivec2 p=clamp(ivec2(vUv*vec2(size)),ivec2(0),size-1);
      vec4 center=texelFetch(map,p,0);
      vec3 color=center.rgb;
      if(traceSamples>=8.0 && center.a>.98) {
        float converged=sqrt(clamp((traceSamples-8.0)/120.0,0.0,1.0));
        color=filterRadiance(p,size-1,color,converged);
      }
      #ifdef TONE_MAPPING
        color=toneMapping(color);
      #endif
      gl_FragColor=linearToOutputTexel(vec4(color,center.a));
      #include <premultiplied_alpha_fragment>
    }`;
}

// Display-referred bilateral filtering of the worker's completed path samples.
// The path accumulator is never a render target of this pass.
export function traceDisplayMaterial(vertexShader) {
  return new THREE.RawShaderMaterial({
    uniforms:{
      source:{value:null}, raster:{value:null}, linearMean:{value:false},
      texel:{value:new THREE.Vector2()}, sigma:{value:0.1}, strength:{value:0.75},traceWeight:{value:1},
    },
    vertexShader,
    fragmentShader:`precision highp float;
      uniform sampler2D source, raster;
      uniform bool linearMean;
      uniform vec2 texel;
      uniform float sigma, strength, traceWeight;
      varying vec2 uv;

      vec4 surfaceAt(vec2 p) {
        vec4 value=texture2D(source,p);
        if (value.a>0.001) value.rgb=clamp(value.rgb/value.a,0.0,1.0);
        return value;
      }

      void gather(vec4 neighbor, vec3 guide, float coverage, float spatial, inout vec3 sum, inout float total) {
        vec3 delta = neighbor.rgb - guide;
        float luminanceDelta = dot(delta,vec3(0.2126,0.7152,0.0722));
        float distance2 = 0.5*luminanceDelta*luminanceDelta + 0.5*dot(delta,delta)/3.0;
        float weight = spatial * exp(-0.5*distance2/(sigma*sigma));
        // Accumulate premultiplied color and coverage with the same weights.
        // Samples across the transparent product mask contribute no color.
        weight *= neighbor.a * (1.0-smoothstep(0.04,0.40,abs(neighbor.a-coverage)));
        sum += neighbor.rgb * weight;
        total += weight;
      }

      vec3 filterProduct(vec2 pos, vec4 center) {
        vec4 ring[8];
        ring[0]=surfaceAt(pos+vec2(texel.x,0.0));
        ring[1]=surfaceAt(pos-vec2(texel.x,0.0));
        ring[2]=surfaceAt(pos+vec2(0.0,texel.y));
        ring[3]=surfaceAt(pos-vec2(0.0,texel.y));
        ring[4]=surfaceAt(pos+texel);
        ring[5]=surfaceAt(pos-texel);
        ring[6]=surfaceAt(pos+vec2(texel.x,-texel.y));
        ring[7]=surfaceAt(pos-vec2(texel.x,-texel.y));
        vec3 low=vec3(1.0), high=vec3(0.0);
        int covered=0;
        for (int i=0;i<8;i++) {
          if (abs(ring[i].a-center.a)<0.12) {
            low=min(low,ring[i].rgb); high=max(high,ring[i].rgb); covered++;
          }
        }
        // Limit isolated outliers only when most neighbors agree. A thin line
        // or a specular edge with matching neighbors keeps its original range.
        vec3 guide=center.rgb;
        if (covered>=6 && max(max(high.r-low.r,high.g-low.g),high.b-low.b)<0.45) {
          guide=clamp(guide,low-0.035,high+0.035);
        }
        vec3 sum=guide*center.a;
        float total=center.a;
        for (int i=0;i<8;i++) gather(ring[i],guide,center.a,i<4 ? 0.78 : 0.56,sum,total);
        gather(surfaceAt(pos+vec2(texel.x*2.0,0.0)),guide,center.a,0.29,sum,total);
        gather(surfaceAt(pos-vec2(texel.x*2.0,0.0)),guide,center.a,0.29,sum,total);
        gather(surfaceAt(pos+vec2(0.0,texel.y*2.0)),guide,center.a,0.29,sum,total);
        gather(surfaceAt(pos-vec2(0.0,texel.y*2.0)),guide,center.a,0.29,sum,total);
        return mix(center.rgb,sum/max(total,0.0001),strength);
      }

      void main() {
        vec2 pos=vec2(uv.x,1.0-uv.y);
        vec4 center=surfaceAt(pos);
        vec3 product=center.rgb;
        if (center.a>0.001 && strength>0.0) product=filterProduct(pos,center);
        vec3 plate=texture2D(raster,uv).rgb;
        if (linearMean) plate=mix(12.92*plate,1.055*pow(plate,vec3(1.0/2.4))-0.055,step(vec3(0.0031308),plate));
        gl_FragColor=vec4(mix(plate,product,center.a*traceWeight),1.0);
      }`,
    depthTest:false, depthWrite:false, blending:THREE.NoBlending, toneMapped:false,
  });
}

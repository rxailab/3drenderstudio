import * as THREE from 'three';
import {traceDisplayMaterial} from './viewport-trace-display.js';

// Pixel-center first, then balanced pairs covering a 3 × 3 subpixel grid.
const offsets = [[0,0],[-1,-1],[1,1],[1,-1],[-1,1],[-1,0],[1,0],[0,-1],[0,1]];
const idleDelay = 120;

export class ViewportRefinement {
  constructor(renderer, frame) {
    this.renderer = renderer;
    this.frame = frame;
    this.count = 0;
    frame.dataset.samples = '0';
    frame.dataset.sampleLimit = String(offsets.length);
    this.projection = new THREE.Matrix4();
    this.inverse = new THREE.Matrix4();
    this.camera = new THREE.Camera();
    this.scene = new THREE.Scene();
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute([-1,-1,0, 3,-1,0, -1,3,0], 3));
    this.material = new THREE.RawShaderMaterial({
      uniforms: { source: { value: null }, weight: { value: 1 }, present: { value: false }, linearMean: { value: false } },
      vertexShader: `attribute vec3 position;
        varying vec2 uv;
        void main() { uv = position.xy * 0.5 + 0.5; gl_Position = vec4(position, 1.0); }`,
      fragmentShader: `precision highp float;
        varying vec2 uv;
        uniform sampler2D source;
        uniform float weight;
        uniform bool present, linearMean;
        void main() {
          vec3 c = texture2D(source,uv).rgb;
          if (linearMean) {
            if (present) c = mix(12.92*c, 1.055*pow(c,vec3(1.0/2.4))-0.055, step(vec3(0.0031308),c));
            else c = mix(c/12.92, pow((c+0.055)/1.055,vec3(2.4)), step(vec3(0.04045),c));
          }
          gl_FragColor = vec4(c, weight);
        }`,
      depthTest: false, depthWrite: false, transparent: true, toneMapped: false,
      blending: THREE.CustomBlending, blendSrc: THREE.SrcAlphaFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
    });
    this.triangle = new THREE.Mesh(geometry, this.material);
    this.triangle.frustumCulled = false;
    this.scene.add(this.triangle);
  }

  pause() {
    clearTimeout(this.timer);
    cancelAnimationFrame(this.raf);
    this.timer = this.raf = 0;
  }

  reset() {
    this.pause();
    if (this.count) this.frame.dataset.samples = '0';
    this.count = 0;
  }

  start(scene, camera, firstSample) {
    if (this.timer || this.raf || this.count === offsets.length) return;
    const next = () => {
      this.raf = 0;
      if (document.hidden) return;
      this.sample(scene, camera);
      // The first raster sample can already support the trace compositing
      // pass. Prepare the worker while the remaining AA samples are drawn.
      if (this.count === 1) firstSample?.();
      if (this.count < offsets.length) this.raf = requestAnimationFrame(next);
    };
    this.timer = setTimeout(() => {
      this.timer = 0;
      this.raf = requestAnimationFrame(next);
    }, idleDelay);
  }

  prepare(width, height) {
    if (this.mean?.width === width && this.mean.height === height) return;
    this.sampleTexture?.dispose();
    this.mean?.dispose();
    this.sampleTexture = new THREE.FramebufferTexture(width, height);
    const halfFloat = this.renderer.extensions.has('EXT_color_buffer_float') || this.renderer.extensions.has('EXT_color_buffer_half_float');
    this.mean = new THREE.WebGLRenderTarget(width, height, {
      depthBuffer: false, type: halfFloat ? THREE.HalfFloatType : THREE.UnsignedByteType,
      minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter,
    });
    this.material.uniforms.linearMean.value = halfFloat;
  }

  sample(scene, camera) {
    const {width, height} = this.renderer.domElement;
    this.prepare(width, height);
    this.projection.copy(camera.projectionMatrix);
    this.inverse.copy(camera.projectionMatrixInverse);
    const [x, y] = offsets[this.count];
    const p = camera.projectionMatrix.elements;
    // Translate clip xy by a fraction of clip w, for both projection types.
    for (let i = 0; i < 16; i += 4) {
      p[i] += 2*x*p[i+3]/(3*width);
      p[i+1] += 2*y*p[i+3]/(3*height);
    }
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
    try {
      this.renderer.render(scene, camera);
    } finally {
      camera.projectionMatrix.copy(this.projection);
      camera.projectionMatrixInverse.copy(this.inverse);
    }
    // Capture the normal MSAA/PBR output, including glass and tone mapping.
    // All intermediate pixels stay on the GPU. The mean needs no depth buffer.
    this.renderer.copyFramebufferToTexture(this.sampleTexture);
    this.pass(this.mean, this.sampleTexture, 1/(this.count+1));
    this.present();
    this.frame.dataset.samples = String(++this.count);
  }

  pass(target, source, weight) {
    const {material} = this;
    material.uniforms.source.value = source;
    material.uniforms.weight.value = weight;
    material.uniforms.present.value = target === null;
    this.drawQuad(target,material);
  }

  presentTrace(texture,samples,limit=8) {
    // A warm worker may finish before all nine raster samples. Preserve the
    // accumulated raster texture and let traced frames own the canvas now.
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.traceDisplay ||= traceDisplayMaterial(this.material.vertexShader);
    const u=this.traceDisplay.uniforms, root=Math.sqrt(Math.max(1,samples));
    u.source.value=texture;u.raster.value=this.mean.texture;
    u.linearMean.value=this.material.uniforms.linearMean.value;
    u.texel.value.set(1/texture.image.width,1/texture.image.height);
    u.sigma.value=.045+Math.min(.12,.52/root);
    // Once the worker filters HDR radiance, just composite the bitmap.
    // Applying another spatial kernel here would attenuate the same detail twice.
    u.strength.value=samples<8 ? .50+.42/(1+root/11) : 0;
    // Keep the clean interactive image while the first paths are noisy. A
    // low sampling cap must still finish showing the fully traced product.
    const t=Math.min(1,samples/Math.min(8,limit));
    u.traceWeight.value=t*(1.6-.6*t);
    this.drawQuad(null,this.traceDisplay);
  }

  drawQuad(target,material) {
    const {renderer}=this;
    const previous = renderer.getRenderTarget(), autoClear = renderer.autoClear;
    try {
      renderer.autoClear = false;
      this.triangle.material=material;
      renderer.setRenderTarget(target);
      renderer.render(this.scene, this.camera);
    } finally {
      renderer.setRenderTarget(previous);
      renderer.autoClear = autoClear;
      this.triangle.material=this.material;
    }
  }

  present() { this.pass(null, this.mean.texture, 1); }

  dispose() {
    this.reset();
    this.sampleTexture?.dispose();
    this.mean?.dispose();
    this.triangle.geometry.dispose();
    this.material.dispose();
    this.traceDisplay?.dispose();
  }
}

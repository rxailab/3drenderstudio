// Wood in blender/catalog_shader.py uses Blender's Wave Texture set to Z rings.
// The lattice hash is Bob Jenkins' public-domain lookup3 finalizer. The noise
// constants and wave convention match Cycles' svm/noise.h and svm/wave.h.
// Each sample carries its analytic gradient for bump mapping on curved surfaces.
export const woodWaveGLSL = /* glsl */ `
uint wyRotateBits(uint n, uint bits) {
  return (n << bits) | (n >> (32u - bits));
}
uint wyWoodHash(ivec3 cell) {
  uvec3 s = uvec3(cell) + uvec3(0xdeadbeefu + 25u);
  s.z = (s.z ^ s.y) - wyRotateBits(s.y, 14u);
  s.x = (s.x ^ s.z) - wyRotateBits(s.z, 11u);
  s.y = (s.y ^ s.x) - wyRotateBits(s.x, 25u);
  s.z = (s.z ^ s.y) - wyRotateBits(s.y, 16u);
  s.x = (s.x ^ s.z) - wyRotateBits(s.z, 4u);
  s.y = (s.y ^ s.x) - wyRotateBits(s.x, 14u);
  return (s.z ^ s.y) - wyRotateBits(s.y, 24u);
}
vec4 wyWoodCorner(ivec3 cell, vec3 f, ivec3 corner) {
  uint h = wyWoodHash(cell + corner) & 15u;
  vec3 x = vec3(1, 0, 0), y = vec3(0, 1, 0), z = vec3(0, 0, 1);
  vec3 u = h < 8u ? x : y;
  vec3 v = h < 4u ? y : ((h == 12u || h == 14u) ? x : z);
  vec3 g = ((h & 1u) == 0u ? u : -u) + ((h & 2u) == 0u ? v : -v);
  return vec4(g, dot(g, f - vec3(corner)));
}
vec4 wyWoodBlend(vec4 a, vec4 b, float weight, vec3 slope) {
  return mix(a, b, weight) + vec4((b.w - a.w) * slope, 0);
}
vec4 wyWoodNoise(vec3 p) {
  ivec3 cell = ivec3(floor(p));
  vec3 f = fract(p);
  vec3 t = f*f*f*(f*(f*6.0-15.0)+10.0);
  vec3 dt = 30.0*f*f*(f-1.0)*(f-1.0);
  vec3 dx = vec3(dt.x,0,0), dy = vec3(0,dt.y,0), dz = vec3(0,0,dt.z);
  vec4 a = wyWoodBlend(wyWoodCorner(cell,f,ivec3(0,0,0)), wyWoodCorner(cell,f,ivec3(1,0,0)), t.x,dx);
  vec4 b = wyWoodBlend(wyWoodCorner(cell,f,ivec3(0,1,0)), wyWoodCorner(cell,f,ivec3(1,1,0)), t.x,dx);
  vec4 c = wyWoodBlend(wyWoodCorner(cell,f,ivec3(0,0,1)), wyWoodCorner(cell,f,ivec3(1,0,1)), t.x,dx);
  vec4 d = wyWoodBlend(wyWoodCorner(cell,f,ivec3(0,1,1)), wyWoodCorner(cell,f,ivec3(1,1,1)), t.x,dx);
  return 0.982 * wyWoodBlend(wyWoodBlend(a,b,t.y,dy), wyWoodBlend(c,d,t.y,dy), t.z,dz);
}
vec4 wyWoodWaveGradient(vec3 generated, float scale, float rotationRadians) {
  float c = cos(rotationRadians), s = sin(rotationRadians);
  mat2 rot = mat2(c,s,-s,c);
  vec3 p = (vec3(rot * generated.xy, generated.z)*scale + 0.000001)*0.999999;
  float radius = length(p.xy);
  vec4 ring = vec4(p.xy/max(radius,1e-12), 0, radius)*20.0;
  // The Wave node's default Detail=2 and Roughness=0.5 give three octaves.
  // Frequency doubles as amplitude halves, so their gradient factors are one.
  vec4 n0 = wyWoodNoise(p), n1 = wyWoodNoise(2.0*p), n2 = wyWoodNoise(4.0*p);
  vec4 distortion = vec4(n0.xyz + n1.xyz + n2.xyz, n0.w + 0.5*n1.w + 0.25*n2.w)/1.75;
  vec4 phase = ring + 4.0*distortion;
  vec3 gradient = phase.xyz*(0.5*sin(phase.w)*scale*0.999999);
  return vec4(transpose(rot)*gradient.xy, gradient.z, 0.5 - 0.5*cos(phase.w));
}
`;

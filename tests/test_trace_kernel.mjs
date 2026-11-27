import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {sceneKernel,kernelForFeatures,traceKernelFeatures,validKernelFeatures} from '../static/viewport-trace-kernel.js';

// Run the specialization against the exact GLSL strings in the pinned bundle.
// This catches changes to shader names and branches on a vendor upgrade.
const vendor = await readFile(new URL('../static/vendor/pathtracer/webgl-pathtracer.js',import.meta.url),'utf8');
const shader = ['material_struct','bsdf_functions','direct_light_contribution_function','get_surface_record_function'].map(name=>{
  const match = vendor.match(new RegExp('var '+name+' = \\(\\s*/\\*\\s*glsl\\s*\\*/\\s*`([\\s\\S]*?)`\\s*\\)'));
  assert.ok(match,name);
  return match[1];
}).join('\n');

// The path loop also lives inside the vendor's generated shader template.
const loop = vendor.slice(vendor.indexOf('// path tracing state'),vendor.indexOf('gl_FragColor.a *= opacity;'));

test('warm programs depend on active shader branches, independently of colors, asset identity and order',()=>{
  const first=[{transmission:1,roughness:.01},{map:{isTexture:true},color:'red'}];
  const second=[{map:{isTexture:true},color:'blue',name:'different'}, {transmission:1,roughness:.015}];
  const key=traceKernelFeatures(first);
  assert.equal(traceKernelFeatures(second),key);
  assert.equal(kernelForFeatures(shader+loop,key),sceneKernel(shader+loop,second));
  assert.notEqual(traceKernelFeatures([...first,{clearcoat:1}]),key);
  assert.notEqual(traceKernelFeatures([{transmission:1,roughness:.5},first[1]]),key);
  assert.equal(validKernelFeatures(key),true);
  for(const bad of [-1,2**24,'12',null,.5])assert.equal(validKernelFeatures(bad),false);
});

test('polished samples own their area-light and environment estimator at the next hit',()=>{
  const kernel=sceneKernel(shader+loop,[{roughness:.008,transmission:1},{roughness:.025,metalness:1}]);
  assert.match(kernel,/ScatterRecord wySamplePolished\(/);
  assert.match(kernel,/directLightContribution\([^\n]+bool polished\)/);
  assert.match(kernel,/directLightContribution\( - ray.direction, surf, state, hitPoint, polished \)/);
  assert.match(kernel,/float misWeight = polished \? 1\.0 : misHeuristic\( scatterRec.pdf, lightRec.pdf/);
  assert.match(kernel,/float misWeight = polished \? 1\.0 : misHeuristic\( scatterRec.pdf, envPdf/);
  assert.match(kernel,/if \(polished\) scatterRec = wySamplePolished/);
  assert.match(kernel,/state.firstRay && \(!state.transmissiveRay \|\| polished\)/);
  assert.doesNotMatch(sceneKernel(shader+loop,[{roughness:.4,transmission:1}]), /wySamplePolished/);
});

test('a scalar-only scene bypasses absent spectral lobes and map transforms',()=>{
  const kernel=sceneKernel(shader,[{metalness:1},{transmission:1}]);
  assert.doesNotMatch(kernel,/vec3 iridescenceF = evalIridescence|color \*= mix\( 1.0, sheenAlbedoScaling/);
  assert.match(kernel,/m\.mapTransform = mat3\(1\.0\);/);
  assert.match(kernel,/float transmission = material\.transmission;/);
  assert.equal(kernel.match(/!\s*attenuateHit\(/g)?.length,1);
});

test('adding an advanced material restores its texture transforms and BRDF terms',()=>{
  const texture={isTexture:true};
  const kernel=sceneKernel(shader,[{metalness:1},{sheen:1,iridescence:.8,clearcoat:.2,map:texture,sheenColorMap:texture}]);
  assert.match(kernel,/vec3 iridescenceF = evalIridescence/);
  assert.match(kernel,/color \*= mix\( 1.0, sheenAlbedoScaling/);
  assert.match(kernel,/clearcoatWeight = surf\.clearcoat \*/);
  assert.match(kernel,/m\.mapTransform = m\.map == - 1/);
  assert.match(kernel,/material\.sheenColorMap != - 1/);
  assert.match(kernel,/m\.normalMapTransform = mat3\(1\.0\);/);
});

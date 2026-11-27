// Sample the same lights, PDFs and random dimensions as the upstream MIS
// integrator. Put the shared transparency/occlusion traversal and BSDF
// evaluation after the light choice. D3D shader compilation otherwise inlines
// two copies of the full BVH traversal, even for a simple studio rig.
const directLight = `
vec3 directLightContribution(vec3 worldWo, SurfaceRecord surf, RenderState state, vec3 rayOrigin) {
  vec3 result = vec3(0.0);
  vec3 direction = vec3(0.0), emission = vec3(0.0);
  float pdf = 0.0, distanceToLight = INFINITY;
  bool deltaLight = false;
  if (lightsDenom != 0.0 && rand(5) < float(lights.count) / lightsDenom) {
    LightRecord light = randomLightSample(lights.tex, iesProfiles, lights.count, rayOrigin, rand3(6));
    direction = light.direction;
    distanceToLight = light.dist;
    emission = light.emission;
    pdf = light.pdf;
    deltaLight = light.type == SPOT_LIGHT_TYPE || light.type == DIR_LIGHT_TYPE || light.type == POINT_LIGHT_TYPE;
  } else if (envMapInfo.totalSum != 0.0 && environmentIntensity != 0.0) {
    pdf = sampleEquirectProbability(rand2(7), emission, direction);
    direction = invEnvRotation3x3 * direction;
    emission *= environmentIntensity;
  }
  if (pdf > 0.0 && (surf.volumeParticle || dot(surf.faceNormal,direction) >= 0.0)
      && isDirectionValid(direction,surf.normal,surf.faceNormal)) {
    Ray ray;
    ray.origin = rayOrigin;
    ray.direction = direction;
    vec3 transmittance;
    if (!attenuateHit(state,ray,distanceToLight,transmittance)) {
      vec3 color;
      float bsdfPdf = bsdfResult(worldWo,direction,surf,color);
      if (bsdfPdf > 0.0 && all(greaterThanEqual(color,vec3(0.0)))) {
        pdf /= lightsDenom;
        float weight = deltaLight ? 1.0 : misHeuristic(pdf,bsdfPdf);
        result = transmittance * emission * state.throughputColor * color * weight / pdf;
      }
    }
  }
  return result;
}`;

// A narrow GGX lobe samples the softbox far more reliably from the surface
// than by guessing a point on the softbox. Keep its actual roughness and GGX
// visible-normal distribution, and choose glass reflection with its Fresnel
// probability at that microfacet (including total internal reflection).
const polishedSample = `
ScatterRecord wySamplePolished(vec3 worldWo, SurfaceRecord surf) {
  vec3 wo = normalize(surf.normalInvBasis * worldWo);
  float alpha = surf.filteredRoughness;
  vec3 h = ggxDirection(wo, vec2(alpha), rand2(12));
  float cosine = clamp(dot(wo, h), 0.0, 1.0);
  ScatterRecord result;
  result.pdf = 1.0;
  result.specularPdf = 1.0;
  vec3 wi;
  if (surf.metalness == 1.0) {
    wi = reflect(-wo, h);
    result.color = schlickFresnel(cosine, surf.color);
  } else {
    float fresnel = dielectricFresnel(cosine, surf.eta);
    if (rand(15) < fresnel || fresnel >= 1.0) {
      wi = reflect(-wo, h);
      result.color = vec3(1.0);
    } else {
      wi = refract(-wo, h, surf.eta);
      // Principled tints each boundary by sqrt(base color); a closed glass
      // object then has the requested tint across a complete traversal.
      result.color = sqrt(clamp(surf.color, 0.0, 1.0));
      result.specularPdf = 0.0;
    }
  }
  // G2 / G1 for visible-normal sampling. The normal distribution and the
  // reflection/refraction Jacobian cancel against the sampled direction PDF.
  // Radiance transport through an interface has the squared IOR ratio; the
  // factors cancel at the two boundaries of a closed pane.
  float a2 = alpha * alpha;
  float go = sqrt(1.0 + a2 * max(0.0, 1.0 / max(wo.z * wo.z, 1e-12) - 1.0));
  float gi = sqrt(1.0 + a2 * max(0.0, 1.0 / max(wi.z * wi.z, 1e-12) - 1.0));
  result.color *= (1.0 + go) / (go + gi);
  if (result.specularPdf == 0.0) result.color *= surf.eta * surf.eta;
  if ((wi.z > 0.0) != (result.specularPdf > 0.0)) result.color = vec3(0.0);
  result.direction = normalize(surf.normalBasis * wi);
  return result;
}`;

function polishedKernel(source) {
  return source
    .replace('vec3 directLightContribution(', polishedSample + '\nvec3 directLightContribution(')
    .replace('RenderState state, vec3 rayOrigin) {','RenderState state, vec3 rayOrigin, bool polished) {')
    .replace('direction = light.direction;', `if (polished && (light.type == RECT_AREA_LIGHT_TYPE || light.type == CIRC_AREA_LIGHT_TYPE)) return result;
    direction = light.direction;`)
    .replace('pdf = sampleEquirectProbability(rand2(7)', 'if (polished) return result;\n    pdf = sampleEquirectProbability(rand2(7)')
    .replace('ScatterRecord scatterRec;', 'ScatterRecord scatterRec;\n bool polished = false;')
    .replace('! state.firstRay && ! state.transmissiveRay', '! state.firstRay && (!state.transmissiveRay || polished)')
    .replace('misHeuristic( scatterRec.pdf, lightRec.pdf / lightsDenom )', 'polished ? 1.0 : misHeuristic( scatterRec.pdf, lightRec.pdf / lightsDenom )')
    .replace('misHeuristic( scatterRec.pdf, envPdf )', 'polished ? 1.0 : misHeuristic( scatterRec.pdf, envPdf )')
    .replace('scatterRec = bsdfSample( - ray.direction, surf );', `
      polished = !surf.volumeParticle && surf.filteredRoughness <= 0.001 &&
          surf.sheen == 0.0 && surf.clearcoat == 0.0 && surf.iridescence == 0.0 &&
          (surf.metalness == 1.0 || (surf.transmission == 1.0 && surf.metalness == 0.0 &&
           !surf.thinFilm && surf.specularIntensity == 1.0 && all(equal(surf.specularColor,vec3(1.0)))));
      if (polished) scatterRec = wySamplePolished(-ray.direction, surf);
      else scatterRec = bsdfSample(-ray.direction, surf);`)
    .replace('directLightContribution( - ray.direction, surf, state, hitPoint )',
      'directLightContribution( - ray.direction, surf, state, hitPoint, polished )');
}

// The shipped integrator reads up to sixteen texture transforms for every hit,
// even when none of the scene's materials has a texture. Tell the compiler
// which channels really exist. Rebuild the variant whenever setScene changes
// the materials, using the original shader as the source on every revision.
const maps = ['map','metalnessMap','roughnessMap','transmissionMap','emissiveMap',
    'normalMap','clearcoatMap','clearcoatNormalMap','clearcoatRoughnessMap',
    'sheenColorMap','sheenRoughnessMap','iridescenceMap','iridescenceThicknessMap',
    'specularColorMap','specularIntensityMap','alphaMap'];
const flags = ['vertexColors','flatShading','transparent'];
const lobes = ['iridescence','clearcoat','sheen','transmission'];
// The ordering is part of the stored warm-up profile. Changing these indices
// requires changing the profile version. Numeric colors and texture data are uniforms.
const featureNames = [...maps,...flags,...lobes,'polished'];
const bits = new Map(featureNames.map((name,index)=>[name,2**index]));

export function traceKernelFeatures(materials) {
  let features=0;
  for (const m of materials) {
    for (const field of maps) if (m[field]?.isTexture) features |= bits.get(field);
    for (const field of flags) if (m[field]) features |= bits.get(field);
    for (const field of lobes) if (m[field]) features |= bits.get(field);
    if ((m.roughness <= Math.sqrt(.001) || m.roughnessMap) &&
        (m.metalness === 1 || m.transmission === 1)) features |= bits.get('polished');
  }
  return features;
}

export function validKernelFeatures(value) {
  return Number.isInteger(value) && value>=0 && value<2**featureNames.length;
}

export function sceneKernel(source,materials) {
  return kernelForFeatures(source,traceKernelFeatures(materials));
}

export function kernelForFeatures(source,features) {
  const constants = new Map();
  for (const field of maps) {
    if (features & bits.get(field)) continue;
    constants.set(field,'(-1)');
    constants.set(field+'Transform','mat3(1.0)');
  }
  for (const field of flags) {
    if (!(features & bits.get(field))) constants.set(field,'false');
  }
  // Preserve all lobes present anywhere in the scene, including materials
  // reached only by reflection or transmission.
  for (const field of lobes) {
    if (!(features & bits.get(field))) constants.set(field,'0.0');
  }
  let result = source
    .replace(/^\tvec3 directLightContribution\([^]*?^\t\}/m,directLight)
    .replace(/\bm\.(\w+) = [^;\n]+;/g,(assignment,field) =>
      constants.has(field) ? 'm.'+field+' = '+constants.get(field)+';' : assignment)
    .replace(/\bmaterial\.(\w+)\b/g,(read,field) => constants.get(field) ?? read);
  // These functions are evaluated even when their mixture weight is zero.
  // Explicitly discard the inactive spectral / velvet terms before ANGLE
  // flattens and inlines the integrator for D3D.
  if (constants.has('iridescence')) {
    result = result.replace(/^[ \t]*vec3 iridescenceF = evalIridescence\([^;]+;\s*F = mix\( F, iridescenceF,[^;]+;/m,'');
  }
  if (constants.has('sheen')) {
    result = result.replace(/^[ \t]*color \*= mix\( 1.0, sheenAlbedoScaling\([^;]+;\s*color \+= sheenColor\([^;]+;/m,'');
  }
  if (constants.has('clearcoat')) {
    // The clearcoat lobe has zero sampling probability and contributes no
    // radiance. Compiling that fact also drops its extra tangent basis.
    result = result.replace(/clearcoatWeight = surf.clearcoat \* [^;]+;/,'clearcoatWeight = 0.0;')
      .replace('clearcoatWi.z >= 0.0 && clearcoatWeight > 0.0','false')
      .replace('r <= cdf[3] ) { // clearcoat','false ) { // clearcoat');
  }
  if (features & bits.get('polished')) result = polishedKernel(result);
  return result;
}

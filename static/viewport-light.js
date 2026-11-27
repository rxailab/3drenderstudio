import * as THREE from './vendor/three/build/three.module.js';

export function addStudioLight(scene,s,bounds) {
  const color=new THREE.Color().fromArray(s.color || [1,1,1]);
  const energy=Math.max(0,s.energy ?? 0);
  if (!energy || !Array.isArray(s.position)) return;
  const position=new THREE.Vector3().fromArray(s.position);
  const target=Array.isArray(s.direction) ? position.clone().add(new THREE.Vector3().fromArray(s.direction)) :
    new THREE.Vector3().fromArray(s.target || bounds.center);
  let light;
  if (s.type==='AREA') {
    const width=Math.max(1e-8,s.width_world ?? s.size ?? bounds.diagonal);
    const rectangular=s.shape==='RECTANGLE' || s.shape==='ELLIPSE';
    const height=Math.max(1e-8,s.height_world ?? (rectangular ? s.size_y ?? width : width));
    const circular=s.shape==='DISK' || s.shape==='ELLIPSE';
    const area=width*height*(circular ? Math.PI/4 : 1);
    // A Lambertian emitter with radiance L emits flux pi * area * L.
    light=new THREE.RectAreaLight(color,energy/(Math.PI*area),width,height);
    light.isCircular=circular;
    light.position.copy(position);
    light.lookAt(target);
    if (s.axis_x && s.axis_y) {
      const u=new THREE.Vector3().fromArray(s.axis_x).normalize();
      const v=new THREE.Vector3().fromArray(s.axis_y).normalize();
      const normal=u.clone().cross(v).normalize();
      if (normal.dot(position.clone().sub(target))<0) normal.negate();
      light.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(u,normal.clone().cross(u),normal));
    }
  } else if (s.type==='SUN') {
    light=new THREE.DirectionalLight(color,energy);
  } else if (s.type==='SPOT') {
    const angle=THREE.MathUtils.clamp((s.spot_size ?? Math.PI/2)/2,.001,Math.PI/2);
    light=new THREE.SpotLight(color,energy/(2*Math.PI*(1-Math.cos(angle))),0,angle,s.spot_blend || 0);
    light.radius=s.shadow_soft_size || 0;
  } else {
    light=new THREE.PointLight(color,energy/(4*Math.PI));
  }
  light.position.copy(position);
  light.name=s.name || s.type || 'Studio light';
  if (light.target) {light.target.position.copy(target);scene.add(light.target);}
  scene.add(light);
  return light;
}

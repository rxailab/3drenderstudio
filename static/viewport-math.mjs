// Camera-only math. Units remain those of the Blender scene; no normalization
// is applied to geometry. Blender Z-up -> glTF Y-up is [x, z, -y].
export const blenderToGltf = ([x,y,z]) => [x,z,-y];
export const gltfToBlender = ([x,y,z]) => [x,-z,y];
const rad = value => value*Math.PI/180;
const deg = value => value*180/Math.PI;
const finite = value => typeof value==='number' && Number.isFinite(value);
const vector = value => Array.isArray(value) && value.length===3 && value.every(finite);
const clamp = (value,min,max) => Math.min(max,Math.max(min,value));
export function aspectNumber(value='1:1') {
  if(finite(value) && value>0)return value;
  const parts=String(value).split(':').map(Number);
  return parts.length===2 && parts.every(n=>Number.isFinite(n) && n>0)?parts[0]/parts[1]:1;
}
export function directionFromAngles(azimuth=35,elevation=18) {
  const a=rad(azimuth),e=rad(elevation);
  return [Math.sin(a)*Math.cos(e),Math.sin(e),Math.cos(a)*Math.cos(e)];
}
export function validateBounds(bounds) {
  if(!bounds || !vector(bounds.min) || !vector(bounds.max) || !vector(bounds.center) || !finite(bounds.diagonal) || bounds.diagonal<=0 || bounds.min.some((v,i)=>v>bounds.max[i]))throw new Error('模型范围数据无效，请重新生成实时预览。');
  return bounds;
}
export function fittedOrthoScale(bounds,direction,aspect=1,margin=.1) {
  validateBounds(bounds);
  const [x,y,z]=direction,h=Math.hypot(x,z)||1;
  const right=[z/h,0,-x/h],up=[-y*x/h,h,-y*z/h];
  let halfWidth=0,halfHeight=0;
  for(const px of [bounds.min[0],bounds.max[0]])for(const py of [bounds.min[1],bounds.max[1]])for(const pz of [bounds.min[2],bounds.max[2]]) {
    const p=[px-bounds.center[0],py-bounds.center[1],pz-bounds.center[2]];
    halfWidth=Math.max(halfWidth,Math.abs(p.reduce((n,v,i)=>n+v*right[i],0)));
    halfHeight=Math.max(halfHeight,Math.abs(p.reduce((n,v,i)=>n+v*up[i],0)));
  }
  return Math.max(halfWidth*2,halfHeight*2*aspect,bounds.diagonal*.01)/(1-2*clamp(margin,0,.4));
}
// Returns a Three-compatible world camera (Y-up). Ortho width is horizontal,
// independent of output aspect, matching Blender sensor_fit='HORIZONTAL'.
export function cameraFromShot(shot,bounds,aspect=1,sourceCamera={},margin=.1) {
  validateBounds(bounds);aspect=aspectNumber(aspect);
  const projection=shot.projection==='perspective'?'perspective':'orthographic';
  const azimuth=finite(shot.azimuth)?shot.azimuth:35,elevation=finite(shot.elevation)?clamp(shot.elevation,-80,89.9):18;
  const direction=directionFromAngles(azimuth,elevation);
  const offset=vector(shot.target_offset)?blenderToGltf(shot.target_offset):[0,0,0];
  const target=bounds.center.map((v,i)=>v+offset[i]*bounds.diagonal);
  const sourceDistance=vector(sourceCamera.position) && vector(sourceCamera.target)?Math.hypot(...sourceCamera.position.map((v,i)=>v-sourceCamera.target[i])):0;
  const lens=finite(shot.focal_length)?clamp(shot.focal_length,20,200):70;
  const fitWidth=fittedOrthoScale(bounds,direction,aspect,margin);
  const sameFraming=(!finite(sourceCamera.azimuth) || Math.abs(sourceCamera.azimuth-azimuth)<1e-5) && (!finite(sourceCamera.elevation) || Math.abs(sourceCamera.elevation-elevation)<1e-5) && (!finite(sourceCamera.aspect) || Math.abs(sourceCamera.aspect-aspect)<1e-5);
  const sourceWidth=sameFraming && sourceCamera.projection==='orthographic' && finite(sourceCamera.ortho_scale) && sourceCamera.ortho_scale>0?sourceCamera.ortho_scale:null;
  const width=finite(shot.ortho_scale) && shot.ortho_scale>0?shot.ortho_scale:(sourceWidth || fitWidth);
  const distance=projection==='perspective' && finite(shot.camera_distance) && shot.camera_distance>0?shot.camera_distance:(sourceDistance || Math.max(fitWidth*lens/36,bounds.diagonal*2));
  const sensorWidth=finite(sourceCamera.sensor_width) && sourceCamera.sensor_width>0?sourceCamera.sensor_width:36;
  return {projection,position:target.map((v,i)=>v+direction[i]*distance),target,up:[0,1,0],
    width,height:width/aspect,aspect,distance,focal_length:lens,
    fov:deg(2*Math.atan(sensorWidth/(2*lens*aspect))),near:Math.max(bounds.diagonal*.0001,1e-6),far:Math.max(bounds.diagonal*100,distance+bounds.diagonal*20)};
}
// Capture controls after update(), when target and camera position are final.
export function shotFromCamera(state,bounds) {
  validateBounds(bounds);
  if(!vector(state.position) || !vector(state.target))throw new Error('当前视角数据无效。');
  const delta=state.position.map((v,i)=>v-state.target[i]),distance=Math.hypot(...delta);
  if(!(distance>0))throw new Error('当前视角距离无效。');
  const targetOffset=gltfToBlender(state.target.map((v,i)=>(v-bounds.center[i])/bounds.diagonal));
  if(targetOffset.some(v=>Math.abs(v)>10))throw new Error('画面已移得太远，请将产品移回取景范围。');
  const projection=state.projection==='perspective'?'perspective':'orthographic';
  const result={projection,azimuth:deg(Math.atan2(delta[0],delta[2])),elevation:clamp(deg(Math.atan2(delta[1],Math.hypot(delta[0],delta[2]))),-80,89.9),target_offset:targetOffset};
  if(projection==='orthographic') {
    const width=state.width/(state.zoom || 1);
    if(!finite(width) || width<1e-6 || width>1e9)throw new Error('当前缩放超出可保存范围，请稍微缩回。');
    result.ortho_scale=width;
  } else {
    if(distance<1e-6 || distance>1e9)throw new Error('当前镜头距离超出可保存范围。');
    result.camera_distance=distance;
    result.focal_length=finite(state.focal_length)?clamp(state.focal_length,20,200):70;
  }
  return result;
}

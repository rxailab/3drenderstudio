// Build the scene-linear lighting maps from the same studio model as the viewport.
// RGB half floats, little endian, south pole first; both renderers read these bytes.
import {readFileSync, writeFileSync, mkdirSync} from 'node:fs';
import {join, basename} from 'node:path';
import {DataUtils} from '../static/vendor/three/build/three.module.js';
import {backdropRadiance, radianceFromPhoto} from '../static/viewport-backdrop.js';

const [output, work] = process.argv.slice(2);
const presets=JSON.parse(readFileSync(join(work,'presets.json'),'utf8'));
const angle=35*Math.PI/180;
const direction={x:-Math.sin(angle),z:-Math.cos(angle)};
mkdirSync(join(output,'radiance'),{recursive:true});
for(const preset of presets){
  let texture;
  if(preset.environmentMap){
    const raw=readFileSync(join(output,'environments',basename(preset.environmentMap)));
    const hdr=new Float32Array(raw.buffer,raw.byteOffset,raw.byteLength/4);
    texture=backdropRadiance(preset,null,null,hdr);
  }else if(preset.image){
    texture=radianceFromPhoto(preset,readFileSync(join(work,preset.id+'.rgba')),direction);
  }else{
    texture=backdropRadiance(preset,null,null,null);
  }
  const {data,width,height}=texture.image;
  const bytes=Buffer.alloc(width*height*6);
  for(let i=0,j=0;i<data.length;i+=4){
    for(let c=0;c<3;c++,j+=2)bytes.writeUInt16LE(DataUtils.toHalfFloat(data[i+c]),j);
  }
  writeFileSync(join(output,'radiance',preset.id+'.bin'),bytes);
  texture.dispose();
}

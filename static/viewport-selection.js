import {cloneViewportMaterial} from './viewport-fast-optics.js';

// Keep authored materials intact, even when selected and unselected meshes
// share one. Returning the original also restores its exact emission and maps.
export class SelectionMaterials {
  constructor(){
    this.copies=new Map();
    this.originals=new WeakMap();
  }
  resolve(material,selected){
    const original=this.originals.get(material) || material;
    if(!selected || !original?.emissive)return original;
    let entry=this.copies.get(original);
    if(!entry){
      entry={copy:cloneViewportMaterial(original),version:original.version};
      this.copies.set(original,entry);
      this.originals.set(entry.copy,original);
    }else if(entry.version!==original.version){
      // Image channels can finish loading while this surface is selected.
      entry.copy.copy(original);
      entry.copy.needsUpdate=true;
      entry.version=original.version;
    }
    entry.copy.emissive.setHex(0xd99b43);
    entry.copy.emissiveIntensity=.45;
    return entry.copy;
  }
  dispose(){
    for(const entry of this.copies.values())entry.copy.dispose();
    this.copies.clear();
  }
}

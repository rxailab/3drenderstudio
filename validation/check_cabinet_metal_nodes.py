"""Read the cabinet's saved render scene; never saves or modifies the scene."""
import bpy
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio_store import get, DATA
p=get('project','fb50b3a68d194f28af84b5d7b598599f')
o=next(o for o in reversed(p['outputs']) if o['shot_id']=='hero' and o['version']==9)
path=DATA/o['blend_url'][7:]
bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
results=[]
for ob in bpy.data.objects:
    if ob.type!='MESH':continue
    for slot in ob.material_slots:
        m=slot.material
        if not m or not any(x in m.name for x in ('亮光金属','铝_1')):continue
        shader=next((n for n in m.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None) if m.use_nodes else None
        if shader:
            results.append({'object':ob.name,'material':m.name,'metallic':shader.inputs['Metallic'].default_value,'metallic_linked':shader.inputs['Metallic'].is_linked,'roughness':shader.inputs['Roughness'].default_value,'roughness_linked':shader.inputs['Roughness'].is_linked})
print('CABINET_METAL_READBACK',json.dumps({'path':str(path),'values':results},ensure_ascii=True))

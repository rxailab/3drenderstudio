"""Read-only audit of the actual pre/post cabinet scenes; does not save blends."""
import hashlib
import json
import sqlite3
from pathlib import Path
import bpy

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data'
PROJECT='fb50b3a68d194f28af84b5d7b598599f'
TARGETS={'G-不锈钢台面_2':'亮光金属_6','铝条_7':'铝_1','铝条_8':'铝_1'}

with sqlite3.connect((DATA/'studio.sqlite').as_uri()+'?mode=ro',uri=True) as db:
    project=json.loads(db.execute('SELECT payload FROM records WHERE kind=? AND id=?',('project',PROJECT)).fetchone()[0])
before=next(o for o in reversed(project['outputs']) if o['version']==9 and o['shot_id']=='hero')
after=next(o for o in reversed(project['outputs']) if o['version']==project['version'] and o['shot_id']=='hero')
names=[x['name'] for x in project['analysis']['objects'] if x['type']=='MESH']

def file(url):
    assert url.startswith('/files/')
    p=(DATA/url[7:]).resolve();assert (DATA/PROJECT).resolve() in p.parents and p.is_file();return p

def snapshot(output):
    bpy.ops.wm.open_mainfile(filepath=str(file(output['blend_url'])),load_ui=False,use_scripts=False)
    objects={};targets={}
    for name in names:
        ob=bpy.data.objects[name]; h=hashlib.sha256()
        for vert in ob.data.vertices:h.update(repr(tuple(vert.co)).encode())
        for poly in ob.data.polygons:h.update(repr((tuple(poly.vertices),poly.material_index)).encode())
        for uv in ob.data.uv_layers:
            for entry in uv.data:h.update(repr(tuple(entry.uv)).encode())
        objects[name]={'mesh_hash':h.hexdigest(),'matrix':[list(row) for row in ob.matrix_world]}
        if name in TARGETS:
            slot=next(s for s in ob.material_slots if s.material and s.material.get('_studio_source_material',s.material.name)==TARGETS[name]);mat=slot.material
            out=next(n for n in mat.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output)
            bs=out.inputs['Surface'].links[0].from_node;assert bs.type=='BSDF_PRINCIPLED'
            color=bs.inputs['Base Color'];targets[name]={'material':TARGETS[name],'metallic':bs.inputs['Metallic'].default_value,'roughness':bs.inputs['Roughness'].default_value,'metallic_linked':bs.inputs['Metallic'].is_linked,'roughness_linked':bs.inputs['Roughness'].is_linked,'color':list(color.default_value),'color_links':[(x.from_node.name,x.from_socket.name) for x in color.links]}
    cam=bpy.context.scene.camera
    return {'objects':objects,'targets':targets,'camera':{'matrix':[list(row) for row in cam.matrix_world],'lens':cam.data.lens,'sensor_width':cam.data.sensor_width},'key_shape':bpy.data.lights['Key softbox'].shape}

a=snapshot(before);b=snapshot(after)
assert a['objects']==b['objects'],'Product geometry, UV or transforms changed'
assert a['camera']==b['camera'],'Camera changed'
for name in TARGETS:
    x=a['targets'][name];y=b['targets'][name]
    assert x['color']==y['color'] and x['color_links']==y['color_links'],'Original color changed: '+name
    assert not y['metallic_linked'] and abs(y['metallic']-1)<1e-6
    assert not y['roughness_linked'] and abs(y['roughness']-.28)<1e-5
assert b['key_shape']=='RECTANGLE'
report={'passed':True,'project_id':PROJECT,'before_version':9,'after_version':project['version'],'before_job':before['job_id'],'after_job':after['job_id'],'before':a['targets'],'after':b['targets'],'product_meshes_checked':len(names),'geometry_uv_transforms_preserved':True,'base_colors_preserved':True,'camera_preserved':True,'before_key_shape':a['key_shape'],'after_key_shape':b['key_shape'],'before_blend':str(file(before['blend_url'])),'after_blend':str(file(after['blend_url']))}
(ROOT/'validation/cabinet-metal-fix-live-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print('CABINET_METAL_FIX_VERIFIED',json.dumps({'versions':[9,project['version']],'targets':b['targets'],'meshes':len(names),'key_shape':b['key_shape']},ensure_ascii=True))

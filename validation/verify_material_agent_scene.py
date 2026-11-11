"""Read-only audit of the real UI material-agent run, inside Blender.

Run: blender --background --factory-startup --disable-autoexec --python
     validation/verify_material_agent_scene.py
Reads SQLite in mode=ro and opens existing .blend files without saving them.
The only written file is validation/material-agent-scene-results.json.
"""
import hashlib
import json
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import unquote,urlsplit

import bpy

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
PROJECT='dab3b4cdf73f4232a7c36cb2cbd958e3'
TASK='13a20b1998b448cd9c33b612b8e97044'
BASE_JOB='6fce95da385c4ec4ac1444ab8fd020ba'
FINAL_JOB='355715f9e83742f286458545c15b6178'
TARGET='Canvas body'


def require(condition,message):
    if not condition:raise AssertionError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()


def local_file(url):
    route=unquote(urlsplit(url).path)
    require(route.startswith('/files/'),'Expected local artifact URL')
    path=(DATA/route[len('/files/'):]).resolve()
    require(path.is_relative_to((DATA/PROJECT).resolve()) and path.is_file(),'Artifact missing or outside project')
    return path


def plain(value):
    if value is None or isinstance(value,(str,int,float,bool)):return value
    try:return [plain(v) for v in value]
    except TypeError:return str(value)


def image_signature(image):
    packed=image.packed_file
    return {'name':image.name,'size':list(image.size),'color_space':image.colorspace_settings.name,
            'packed':bool(packed),'packed_sha256':hashlib.sha256(packed.data).hexdigest() if packed else None}


def socket_signature(socket):
    return {'default':plain(getattr(socket,'default_value',None)),
            'links':[(link.from_node.name,link.from_socket.identifier) for link in socket.links]}


def material_signature(material):
    if not material:return None
    nodes=[]
    if material.use_nodes:
        for node in material.node_tree.nodes:
            entry={'name':node.name,'type':node.type,'inputs':[(socket.identifier,socket_signature(socket)) for socket in node.inputs]}
            for prop in ('operation','blend_type','projection','projection_blend','interpolation','extension','uv_map','space'):
                if hasattr(node,prop):entry[prop]=plain(getattr(node,prop))
            if node.type=='TEX_IMAGE' and node.image:entry['image']=image_signature(node.image)
            nodes.append(entry)
    return {'nodes':sorted(nodes,key=lambda n:n['name']),'diffuse_color':list(material.diffuse_color),
            'metallic':material.metallic,'roughness':material.roughness,'use_nodes':material.use_nodes}


def product_signature(objects):
    values={}
    for name in objects:
        ob=bpy.data.objects.get(name)
        require(ob is not None,'Missing product object '+name)
        value={'type':ob.type,'matrix_world':[list(row) for row in ob.matrix_world],
               'materials':[material_signature(slot.material) for slot in ob.material_slots]}
        if ob.type=='MESH':
            value['geometry_sha256']=digest({'vertices':[list(v.co) for v in ob.data.vertices],
                'polygons':[list(poly.vertices) for poly in ob.data.polygons],
                'material_indices':[poly.material_index for poly in ob.data.polygons],
                'uv_layers':[[list(uv.uv) for uv in layer.data] for layer in ob.data.uv_layers]})
        values[name]=value
    return values


def active_shader(material):
    output=next(node for node in material.node_tree.nodes if node.type=='OUTPUT_MATERIAL' and node.is_active_output)
    shader=output.inputs['Surface'].links[0].from_node
    require(shader.type=='BSDF_PRINCIPLED','Expected actual connected Principled shader')
    return shader


def color_branch(shader):
    socket=shader.inputs['Base Color']
    require(socket.is_linked,'Expected original color texture connection')
    tree=shader.id_data
    seen=set();nodes=[]
    def visit(node):
        if node.name in seen:return
        seen.add(node.name)
        entry={'name':node.name,'type':node.type,'inputs':[(s.identifier,socket_signature(s)) for s in node.inputs]}
        if node.type=='TEX_IMAGE':entry['image']=image_signature(node.image)
        nodes.append(entry)
        for s in node.inputs:
            for link in s.links:visit(link.from_node)
    visit(socket.links[0].from_node)
    return {'socket':socket_signature(socket),'nodes':sorted(nodes,key=lambda n:n['name'])}


with sqlite3.connect((DATA/'studio.sqlite').as_uri()+'?mode=ro',uri=True) as connection:
    def record(kind,ident):
        row=connection.execute('SELECT payload FROM records WHERE kind=? AND id=?',(kind,ident)).fetchone()
        require(row is not None,'Missing record '+kind+':'+ident)
        return json.loads(row[0])
    project=record('project',PROJECT);task=record('material_task',TASK)
    base_job=record('job',BASE_JOB);final_job=record('job',FINAL_JOB)
require(task['status']=='completed' and task['job_id']==FINAL_JOB,'Material task did not finish')
require(base_job['status']==final_job['status']=='completed','Render jobs did not finish')
require(task['use_base_color'] is False and task['selection']['asset_id']=='stretch_poplin','Unexpected request or selected resource')
before_output=next(o for o in project['outputs'] if o['job_id']==BASE_JOB and o['shot_id']=='hero' and o['version']==1)
after_output=next(o for o in project['outputs'] if o['job_id']==FINAL_JOB and o['shot_id']=='hero' and o['version']==2)
before_file=local_file(before_output['blend_url']);after_file=local_file(after_output['blend_url'])
before_result=json.loads(local_file(before_output['result_url']).read_text(encoding='utf-8'))
after_result=json.loads(local_file(after_output['result_url']).read_text(encoding='utf-8'))
require(before_result['camera']==after_result['camera'],'Camera changed')
require(before_result['studio']['lights']==after_result['studio']['lights'],'Lighting changed')
objects=[item['name'] for item in project['analysis']['objects']]
require(TARGET in objects and len(objects)>1,'Expected multi-part product')

bpy.ops.wm.open_mainfile(filepath=str(before_file),load_ui=False,use_scripts=False)
before=product_signature(objects)
base_material=bpy.data.objects[TARGET].material_slots[0].material
base_shader=active_shader(base_material)
before_color=color_branch(base_shader)
before_inputs={s.identifier:socket_signature(s) for s in base_shader.inputs if s.name!='Normal'}

bpy.ops.wm.open_mainfile(filepath=str(after_file),load_ui=False,use_scripts=False)
after=product_signature(objects)
for name in objects:
    if name!=TARGET:require(before[name]==after[name],'Unselected object/material changed: '+name)
require(before[TARGET]['geometry_sha256']==after[TARGET]['geometry_sha256'],'Target geometry changed')
require(before[TARGET]['matrix_world']==after[TARGET]['matrix_world'],'Target transform changed')
material=bpy.data.objects[TARGET].material_slots[0].material
shader=active_shader(material)
require(before_color==color_branch(shader),'Original Base Color graph or image content changed')
require(before_inputs=={s.identifier:socket_signature(s) for s in shader.inputs if s.name!='Normal'},'Non-normal shader input changed')
normal=shader.inputs['Normal'].links[0].from_node
require(normal.type=='NORMAL_MAP','Downloaded normal is not connected to the actual surface')
node=normal.inputs['Color'].links[0].from_node
require(node.type=='TEX_IMAGE' and node.name=='External normal','Expected downloaded external normal image')
normal_image=image_signature(node.image)
require(normal_image['color_space']=='Non-Color' and normal_image['packed'],'Normal is not packed Non-Color data')
require(normal_image['packed_sha256']==task['downloaded_files']['normal']['sha256'],'Packed normal does not match downloaded resource')
provenance=json.loads(material['provenance'])
require(provenance['resource']['asset_id']=='stretch_poplin' and provenance['license']=='CC0-1.0','Missing resource/license provenance')
require(provenance['preserves_original_color'] is True,'Original color preservation not recorded')
require(all(image.packed_file for image in bpy.data.images if image.source=='FILE'),'Some scene file images are not packed')
require(any(m['channel']=='normal' and m['applied'] for m in provenance['maps']),'Normal application not recorded')

report={'passed':True,'checked_at':datetime.now(timezone.utc).isoformat(),'blender_version':bpy.app.version_string,
        'project_id':PROJECT,'task_id':TASK,'before_job':BASE_JOB,'after_job':FINAL_JOB,
        'before_scene':str(before_file),'after_scene':str(after_file),'selected_object':TARGET,
        'original_base_color_graph_and_packed_image_unchanged':True,'non_normal_shader_inputs_unchanged':True,
        'selected_geometry_and_transform_unchanged':True,'unselected_objects_and_materials_unchanged':len(objects)-1,
        'camera_and_lights_unchanged':True,'external_normal_connected_to_actual_surface':True,
        'normal_matches_download_sha256':True,'all_file_images_packed':True,'normal_image':normal_image,
        'base_color_images':[n['image'] for n in before_color['nodes'] if 'image' in n],
        'resource':provenance['resource'],'license':provenance['license'],
        'read_only_scene_audit':True}
(ROOT/'validation/material-agent-scene-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('MATERIAL_AGENT_SCENE_VERIFIED real before/after Blender scenes; preserved color and unselected surfaces')

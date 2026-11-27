"""Sample Blender's native 3D catalog wave field on the CPU without rendering.

blender -b --factory-startup --python tests/catalog_wave_reference.py -- output.json
"""
import json
from pathlib import Path
import sys

import bpy
from mathutils import Euler,Vector

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from blender.catalog_shader import _detail


def sample_node(spec, points):
    material=bpy.data.materials.new('Catalog reference')
    material.use_nodes=True
    shader=material.node_tree
    coordinate=shader.nodes.new('ShaderNodeTexCoord')
    source=_detail(shader,spec,coordinate).node
    mapping=source.inputs['Vector'].links[0].from_node
    rotation=Euler(mapping.inputs['Rotation'].default_value[:]).to_matrix()
    scale=mapping.inputs['Scale'].default_value
    mapped=[rotation@Vector([p[i]*scale[i] for i in range(3)]) for p in points]
    mesh=bpy.data.meshes.new('Wave probes')
    mesh.from_pydata(mapped,[],[])
    obj=bpy.data.objects.new('Wave probes',mesh)
    bpy.context.scene.collection.objects.link(obj)
    tree=bpy.data.node_groups.new('Evaluate catalog texture','GeometryNodeTree')
    try:
        for direction in ('INPUT','OUTPUT'):
            tree.interface.new_socket(name='Geometry',in_out=direction,socket_type='NodeSocketGeometry')
        input_node=tree.nodes.new('NodeGroupInput')
        output_node=tree.nodes.new('NodeGroupOutput')
        position=tree.nodes.new('GeometryNodeInputPosition').outputs['Position']
        wave=tree.nodes.new('ShaderNodeTexWave')
        for key in ('wave_type','rings_direction','bands_direction','wave_profile'):
            setattr(wave,key,getattr(source,key))
        for socket in source.inputs:
            if socket.name!='Vector':
                wave.inputs[socket.name].default_value=socket.default_value
        tree.links.new(position,wave.inputs['Vector'])
        attribute=tree.nodes.new('GeometryNodeStoreNamedAttribute')
        attribute.domain='POINT'
        attribute.data_type='FLOAT'
        attribute.inputs['Name'].default_value='native_wave'
        tree.links.new(input_node.outputs['Geometry'],attribute.inputs['Geometry'])
        tree.links.new(wave.outputs['Fac'],attribute.inputs['Value'])
        tree.links.new(attribute.outputs['Geometry'],output_node.inputs['Geometry'])
        mod=obj.modifiers.new('Wave field','NODES')
        mod.node_group=tree
        evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return [item.value for item in evaluated.data.attributes['native_wave'].data]
    finally:
        bpy.data.objects.remove(obj,do_unlink=True)
        bpy.data.meshes.remove(mesh)
        bpy.data.node_groups.remove(tree)
        bpy.data.materials.remove(material)


def reference():
    positions=[(.071,.33,.52),(.2,.1,.7),(.6,.61,.33),(.49,.5,.51),(.9,.01,.88),
               (-.22,.47,1.08),(.031,.0101,.0117),(1.038,-.08,.918)]
    step=.00025
    probes=[]
    for p in positions:
        probes.append(p)
        for axis in range(3):
            for sign in (-1,1):
                q=list(p)
                q[axis]+=sign*step
                probes.append(q)
    rows=[]
    for kind,size,angle in [('wood',5.,0.),('wood',.032,37.),('wood',.048,-23.),('marble',.05,17.)]:
        spec={'type':kind,'size':size,'rotation':angle}
        values=sample_node(spec,probes)
        for i,p in enumerate(positions):
            factor,*offsets=values[7*i:7*i+7]
            gradient=[(offsets[j+1]-offsets[j])/(2*step) for j in (0,2,4)]
            rows.append(dict(type=kind,size=size,rotation_degrees=angle,xyz=p,value=factor,gradient=gradient))
    return {'blender':bpy.app.version_string,'delta':step,'samples':rows}


if __name__=='__main__':
    destination=Path(sys.argv[sys.argv.index('--')+1])
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(reference(),indent=2)+'\n',encoding='utf-8')

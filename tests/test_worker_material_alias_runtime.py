"""Exercise cross-shot material identities against real Blender slots."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import bpy, sys
sys.path.insert(0, sys.argv[sys.argv.index('--') + 1])
from blender.worker import prepare
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
material=bpy.data.materials.new('Paint'); material.use_nodes=True
objects=[]
for name in ('Body','Other'):
    bpy.ops.mesh.primitive_cube_add(); obj=bpy.context.object; obj.name=name
    obj.data.materials.append(material); objects.append(obj)
if sys.argv[-1]=='ambiguous':
    second=bpy.data.materials.new('Paint.001'); second.use_nodes=True
    objects[0].data.materials.append(second)
plan={'rotation':[0,0,0], 'repair':{}, 'material_overrides':[],
      'object_material_overrides':[
        {'object_name':'Body','material_name':'Paint','preset':'rubber','preserve_original':False},
        {'object_name':'Body','material_name':'Paint.008','preset':'polished_metal','preserve_original':False}]}
if sys.argv[-1]=='ambiguous':
    try: prepare(objects,objects,plan)
    except ValueError as exc: assert '局部材质槽不存在' in str(exc)
    else: raise AssertionError('ambiguous slot was accepted')
else:
    operations,_=prepare(objects,objects,plan)
    body=objects[0].material_slots[0].material; other=objects[1].material_slots[0].material
    assert body != other
    assert body['_studio_source_material']=='Paint'
    assert body.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value==1
    assert other.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value==0
    assert len([x for x in operations if x.get('operation')=='object_material_override'])==2
'''


@unittest.skipUnless(shutil.which('blender'), 'Blender unavailable')
class WorkerMaterialAliasRuntimeTests(unittest.TestCase):
    def run_scene(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            script=Path(directory)/'fixture.py';script.write_text(SCRIPT,encoding='utf-8')
            result=subprocess.run([shutil.which('blender'),'-b','--factory-startup','--python',str(script),
                                   '--',str(ROOT),mode],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertNotIn('Traceback',result.stdout+result.stderr)

    def test_previous_slot_alias_is_local_to_target(self): self.run_scene('unique')
    def test_ambiguous_alias_is_rejected(self): self.run_scene('ambiguous')


if __name__ == '__main__': unittest.main()

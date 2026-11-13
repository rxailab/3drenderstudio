"""Real Blender GLB exports: geometry, source immutability, PBR and camera."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from planner import default_plan

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'blender/export_viewport.py'

FIXTURE=r'''
import bpy,json,sys
from pathlib import Path
from mathutils import Vector
root=Path(sys.argv[sys.argv.index('--')+1]);target=root/'source.blend'
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,0,1));body=bpy.context.object;body.name='Body';body.scale=(1.6,1,2)
mat=bpy.data.materials.new('Paint');mat.use_nodes=True;node=mat.node_tree.nodes.get('Principled BSDF')
node.inputs['Base Color'].default_value=(.4,.02,.03,1);node.inputs['Roughness'].default_value=.35;node.inputs['Metallic'].default_value=.6
body.data.materials.append(mat)
trim=bpy.data.materials.new('Trim');trim.use_nodes=True
trim.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=(.02,.02,.02,1)
body.data.materials.append(trim);body.data.polygons[0].material_index=1
bpy.ops.mesh.primitive_cube_add(size=1,location=(0,-.51,1.5));label=bpy.context.object;label.name='Brand label';label.scale=(.6,.02,.18)
logo=bpy.data.materials.new('Logo');logo.use_nodes=True;label.data.materials.append(logo)
image=bpy.data.images.new('Original brand texture',width=2,height=2)
image.pixels=[1,0,0,1,0,1,0,1,0,0,1,1,1,1,1,1];image.pack()
texture=logo.node_tree.nodes.new('ShaderNodeTexImage');texture.image=image
logo.node_tree.links.new(texture.outputs['Color'],logo.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
bpy.ops.mesh.primitive_plane_add(size=100,location=(0,0,-.01));bpy.context.object.name='Studio floor'
bpy.ops.object.camera_add(location=(3,-5,3));camera=bpy.context.object;camera.data.lens=65
camera.rotation_euler=(Vector((0,0,1))-camera.location).to_track_quat('-Z','Y').to_euler();scene.camera=camera
for name,position,energy in [('KeyLight',(-3,-4,5),350),('FillLight',(3,-2,3),120),('RimLight',(1,3,4),180)]:
 data=bpy.data.lights.new(name,'AREA');data.energy=energy;data.size=3;data.shape='RECTANGLE';data.size_y=2
 light=bpy.data.objects.new(name,data);scene.collection.objects.link(light);light.location=position
 light.rotation_euler=(Vector((0,0,1))-light.location).to_track_quat('-Z','Y').to_euler()
scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.25
scene['studio_product_objects']=json.dumps(['Body','Brand label'])
scene.render.resolution_x=128;scene.render.resolution_y=128;scene.render.resolution_percentage=100
bpy.ops.object.select_all(action='DESELECT');scene.objects['Studio floor'].select_set(True)
bpy.ops.wm.save_as_mainfile(filepath=str(target))
'''


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def content(path):
    with path.open('rb') as stream:
        magic,version,total,size,kind=struct.unpack('<4sIIII',stream.read(20))
        assert magic==b'glTF' and version==2 and total==path.stat().st_size
        return json.loads(stream.read(size).decode().rstrip('\0 \t\r\n'))


@unittest.skipUnless(shutil.which('blender'),'Blender unavailable: real viewport export not run')
class ViewportRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name);cls.source=cls.root/'source.blend';fixture=cls.root/'fixture.py';fixture.write_text(FIXTURE)
        result=subprocess.run([shutil.which('blender'),'-b','--factory-startup','--disable-autoexec','-t','2',
                               '--python-exit-code','1','--python',str(fixture),'--',str(cls.root)],
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
        if result.returncode:raise AssertionError(result.stdout[-4000:])
        cls.original_sha=sha(cls.source);cls.sequence=0;cls.baseline=default_plan()

    def invoke(self,source=None,plan=None,baseline=True,success=True):
        type(self).sequence+=1;out=self.root/('viewport-'+str(self.sequence));out.mkdir()
        source=source or self.source;request={'schema':'wuying-viewport-v1','request_id':str(self.sequence).zfill(32),
            'source_path':str(source),'source_sha256':sha(source),'source_kind':'saved_scene' if baseline else 'original_model',
            'project_root':str(self.root),'output_dir':str(out),'plan':copy.deepcopy(plan or self.baseline),
            'baseline_plan':copy.deepcopy(self.baseline) if baseline else None,'shot_id':'hero','product_object_names':['Body','Brand label']}
        path=out/'request.json';path.write_text(json.dumps(request))
        result=subprocess.run([shutil.which('blender'),'-b','--factory-startup','--disable-autoexec','-t','2',
                               '--python-exit-code','1','--python',str(SCRIPT),'--',str(path)],
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90)
        (out/'log.txt').write_text(result.stdout)
        report=json.loads((out/'result.json').read_text()) if (out/'result.json').exists() else {'success':False,'error':result.stdout[-2000:]}
        if success and (result.returncode or report.get('success') is not True):raise AssertionError(str(report)+'\n'+result.stdout[-4000:])
        if not success:self.assertFalse(report.get('success'))
        self.assertEqual(sha(self.source),self.original_sha)
        return out,report

    def test_native_saved_scene_exports_actual_product_without_floor(self):
        out,report=self.invoke();manifest=report['manifest'];data=content(out/'scene.glb')
        self.assertEqual(manifest['geometry']['objects'],2);self.assertEqual(len(data['meshes']),2)
        self.assertFalse(any('floor' in row.get('name','').lower() for row in data.get('nodes',[])))
        self.assertEqual(manifest['camera']['projection'],'orthographic')
        self.assertEqual(manifest['camera']['sensor_fit'],'HORIZONTAL')
        self.assertAlmostEqual(manifest['bounds']['center'][1],1,places=5)
        self.assertAlmostEqual(manifest['bounds_blender']['max'][2],2,places=5)
        self.assertEqual(len(manifest['lights']),3);self.assertEqual(manifest['lights'][0]['type'],'AREA')
        self.assertIn('size_y',manifest['lights'][0]);self.assertEqual(manifest['lighting_fidelity'],'webgl-approximation')
        paint=next(row for row in data['materials'] if row['name']=='Paint')['pbrMetallicRoughness']
        self.assertAlmostEqual(paint['roughnessFactor'],.35,places=5);self.assertAlmostEqual(paint['metallicFactor'],.6,places=5)
        self.assertTrue(data.get('images'));self.assertTrue(all('uri' not in row for row in data['images']))
        self.assertTrue(any('TEXCOORD_0' in primitive['attributes'] for mesh in data['meshes'] for primitive in mesh['primitives']))
        self.assertTrue(any(len(mesh['primitives'])==2 for mesh in data['meshes']))
        self.assertTrue(any(row['name']=='Trim' for row in data['materials']))
        self.assertFalse((out/'scene.blend').exists());self.assertFalse((out/'image.png').exists())

    def test_pending_camera_and_material_changes_are_applied_before_export(self):
        plan=copy.deepcopy(self.baseline);shot=plan['shots'][0]
        shot.update(azimuth=78,elevation=12,ortho_scale=3.7,target_offset=[.1,0,.05])
        plan['material_overrides']=[{'name':'Paint','preset':'paint','preserve_original':True,'roughness':.2}]
        out,report=self.invoke(plan=plan);camera=report['manifest']['camera']
        self.assertAlmostEqual(camera['azimuth'],78,places=4);self.assertAlmostEqual(camera['ortho_scale'],3.7,places=5)
        self.assertAlmostEqual(camera['target_offset'][0],.1,places=5)
        paint=next(row for row in content(out/'scene.glb')['materials'] if row['name'].startswith('Paint'))
        self.assertAlmostEqual(paint['pbrMetallicRoughness']['roughnessFactor'],.2,places=5)

    def test_unapplied_material_preset_never_claims_current_scene(self):
        plan=copy.deepcopy(self.baseline);plan['material_overrides']=[{'name':'Paint','preset':'brushed_metal'}]
        _,report=self.invoke(plan=plan,success=False)
        self.assertIn('材质',report['error'])

    def test_local_preset_is_exported_without_changing_other_slots(self):
        plan=copy.deepcopy(self.baseline)
        plan['object_material_overrides']=[{'object_name':'Body','material_name':'Paint',
                                           'preset':'polished_metal','preserve_original':False}]
        out,report=self.invoke(plan=plan)
        materials={row['name']:row for row in content(out/'scene.glb')['materials']}
        paint=materials['Paint']['pbrMetallicRoughness']
        self.assertAlmostEqual(paint['roughnessFactor'],.09,places=5)
        self.assertAlmostEqual(paint.get('metallicFactor',1),1,places=5) # glTF default
        self.assertIn('Trim',materials)
        self.assertIn('Logo',materials)
        self.assertTrue(any(row['kind']=='existing_surface_preset' for row in report['manifest']['applied_operations']))

    def test_uploaded_glb_is_interactive_without_offline_render(self):
        out,_=self.invoke();source=self.root/'uploaded.glb';shutil.copy2(out/'scene.glb',source)
        result,report=self.invoke(source=source,baseline=False)
        self.assertTrue((result/'scene.glb').is_file())
        self.assertEqual(report['manifest']['camera']['projection'],'orthographic')
        self.assertEqual(report['manifest']['geometry']['objects'],2)
        self.assertTrue(any(row['kind']=='preview_studio' for row in report['manifest']['applied_operations']))

    def test_uploaded_glb_applies_global_and_local_pbr_overrides_before_export(self):
        original,_=self.invoke();source=self.root/'uploaded-materials.glb';shutil.copy2(original/'scene.glb',source)
        source_sha=sha(source);plan=copy.deepcopy(self.baseline)
        # Blender's GLB importer may name an object after its mesh, so resolve
        # the actual imported object exactly as the model analysis does.
        probe,_=self.invoke(source=source,baseline=False);loaded=content(probe/'scene.glb')
        trim_index=next(i for i,row in enumerate(loaded['materials']) if row['name'].startswith('Trim'))
        body=next(row['name'] for row in loaded['nodes'] if 'mesh' in row and
                  any(primitive.get('material')==trim_index for primitive in loaded['meshes'][row['mesh']]['primitives']))
        body=body.removesuffix(' · viewport')
        from PIL import Image
        texture=self.root/'roughness.png';Image.new('RGB',(2,2),(80,80,80)).save(texture)
        plan['material_overrides']=[{'name':'Paint','preset':'brushed_metal','roughness':.22,
                                     'textures':{'roughness':str(texture)}}]
        plan['object_material_overrides']=[{'object_name':body,'material_name':'Trim',
                                           'preset':'polished_metal','preserve_original':False,'roughness':.09}]
        output,report=self.invoke(source=source,plan=plan,baseline=False)
        materials=content(output/'scene.glb')['materials']
        paint=next(row for row in materials if row['name'].startswith('Paint'))['pbrMetallicRoughness']
        trim=next(row for row in materials if row['name'].startswith('Trim'))['pbrMetallicRoughness']
        self.assertIn('metallicRoughnessTexture',paint)
        self.assertAlmostEqual(paint.get('metallicFactor',1),1,places=4)
        self.assertAlmostEqual(trim['roughnessFactor'],.09,places=4)
        self.assertTrue(any(row['name'].startswith('Logo') for row in materials))
        self.assertTrue(content(output/'scene.glb').get('images'))
        self.assertEqual(sha(source),source_sha)
        self.assertTrue(any(row['operation']=='object_material_override' for row in report['manifest']['applied_operations'] if 'operation' in row))
        self.assertTrue(any(row['path']=='roughness.png' for row in report['manifest']['resources']))

    def test_uploaded_glb_rejects_texture_outside_project(self):
        original,_=self.invoke();source=self.root/'uploaded-untrusted.glb';shutil.copy2(original/'scene.glb',source)
        with tempfile.TemporaryDirectory() as directory:
            from PIL import Image
            texture=Path(directory)/'outside.png';Image.new('RGB',(2,2)).save(texture)
            plan=copy.deepcopy(self.baseline)
            plan['material_overrides']=[{'name':'Paint','preset':'matte_plastic','textures':{'roughness':str(texture)}}]
            _,report=self.invoke(source=source,plan=plan,baseline=False,success=False)
            self.assertIn('不在当前项目',report['error'])


if __name__=='__main__':unittest.main()

"""Real Blender fixture checks for the read-only geometry catalog.

The synthetic cabinet is deliberately named with opaque mesh IDs.  These tests
validate measured geometry/slot identities, not AI semantic identification of
the user's actual model.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / 'blender' / 'inspect_parts.py'
SPEC = importlib.util.spec_from_file_location('part_inspector', SCRIPT)
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)


FIXTURE = r'''
import bpy,json,math,sys
from pathlib import Path
from mathutils import Vector
folder=Path(sys.argv[sys.argv.index('--')+1]);folder.mkdir(parents=True,exist_ok=True)
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
shared=bpy.data.materials.new('Shared');shared.diffuse_color=(.45,.45,.45,1)
renamed=bpy.data.materials.new('Rendered_Surface_99');renamed.diffuse_color=(.1,.1,.1,1)
known=[];observed=[];wheel_pairs=[]
def box(name,location,scale,material=True):
    bpy.ops.mesh.primitive_cube_add(size=1,location=location);obj=bpy.context.object;obj.name=name;obj.scale=scale
    if material:obj.data.materials.append(shared)
    known.append({'name':name,'type':'MESH','materials':['Shared'] if material else []})
    return obj
body=box('Mesh.001',(0,0,1.15),(1.65,.9,1.68))
top=box('Mesh.002',(0,0,2.02),(1.75,1.0,.06))
counter=3
for number,(x,y) in enumerate(((-.64,-.31),(.64,-.31),(-.64,.31),(.64,.31))):
    bpy.ops.mesh.primitive_torus_add(major_segments=32,minor_segments=12,major_radius=.12,minor_radius=.035,location=(x,y,.16),rotation=(0,math.pi/2,0))
    torus=bpy.context.object;data=torus.data
    vertices=[tuple(v.co) for v in data.vertices]
    # Split every physical wheel into two real mesh objects at its axial plane.
    for side in (False,True):
        polygons=[list(p.vertices) for p in data.polygons if (sum(vertices[i][2] for i in p.vertices)>=0)==side]
        mesh=bpy.data.meshes.new('OpaqueWheelData');mesh.from_pydata(vertices,[],polygons);mesh.update()
        name='Mesh.%03d'%counter;counter+=1
        obj=bpy.data.objects.new(name,mesh);bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world=torus.matrix_world.copy()
        material=renamed if number in (2,3) else shared;mesh.materials.append(material)
        known.append({'name':name,'type':'MESH','materials':['Shared']});wheel_pairs.append(name)
        if number==2:observed.append({'object_name':name,'material_name':'Shared','material_render_name':renamed.name,'slot_index':0})
    bpy.data.objects.remove(torus,do_unlink=True)
    box('Bracket.%d'%number,(x,y,.31),(.06,.1,.14))
# A single object containing two disconnected actual surfaces must keep both
# measured components; object count must not become physical part count.
mesh=bpy.data.meshes.new('DisconnectedData')
mesh.from_pydata([(2,0,0),(2.1,0,0),(2,0,.1),(2.4,0,0),(2.5,0,0),(2.4,0,.1)],[],[(0,1,2),(3,4,5)])
obj=bpy.data.objects.new('Mesh.Disconnected',mesh);bpy.context.scene.collection.objects.link(obj);mesh.materials.append(shared)
known.append({'name':obj.name,'type':'MESH','materials':['Shared']})
box('Mesh.Slotless',(2.5,.4,.4),(.1,.1,.1),False)
array=box('Mesh.RenderArray',(2.7,.4,.4),(.05,.05,.05))
modifier=array.modifiers.new('RenderOnlyDuplicate','ARRAY');modifier.count=2;modifier.relative_offset_displace=(3,0,0)
modifier.show_viewport=False;modifier.show_render=True
bpy.ops.object.camera_add(location=(3,-5,2.7));camera=bpy.context.object
camera.rotation_euler=(Vector((0,0,1))-camera.location).to_track_quat('-Z','Y').to_euler();bpy.context.scene.camera=camera
bpy.context.view_layer.update();graph=bpy.context.evaluated_depsgraph_get()
hidden=[]
for name in wheel_pairs:
    obj=bpy.data.objects[name]
    # A real ray from the hero camera to each wheel's representative surface.
    point=obj.matrix_world @ obj.data.vertices[0].co
    origin=camera.location;direction=(point-origin).normalized()
    hit,location,normal,index,first,matrix=bpy.context.scene.ray_cast(graph,origin,direction,distance=(point-origin).length+.01)
    if hit and first.name!=name:hidden.append(name)
bpy.ops.wm.save_as_mainfile(filepath=str(folder/'synthetic_cabinet.blend'))
(folder/'fixture.json').write_text(json.dumps({'known_objects':known,'observed_slots':observed,'wheel_names':wheel_pairs,'occluded_from_hero':hidden}))
'''


class InspectorHelpersTests(unittest.TestCase):
    def test_exact_slots_and_measured_aliases_never_expand_shared_material(self):
        self.assertEqual(inspector._slot_map(['Rendered_Surface'], ['Shared'], [
            {'material_name':'Shared','material_render_name':'Rendered_Surface','slot_index':0}]), {'Shared':[0]})
        self.assertEqual(inspector._slot_map(['Rendered_Surface'], ['Shared'], []), {'Shared':[]})
        self.assertEqual(inspector._slot_map(['Shared'], ['Other'], []), {'Other':[]})

    def test_conflicting_aliases_cannot_authorize_one_slot_twice(self):
        values=[{'material_name':name,'material_render_name':'Rendered_Surface','slot_index':0} for name in ('A','B')]
        self.assertEqual(inspector._slot_map(['Rendered_Surface'], ['A','B'], values), {'A':[],'B':[]})

    def test_repeated_rendered_alias_requires_measured_slot_index(self):
        self.assertEqual(inspector._slot_map(['Renamed','Renamed'], ['A'], [
            {'material_name':'A','material_render_name':'Renamed'}]), {'A':[]})
        self.assertEqual(inspector._slot_map(['Renamed','Renamed'], ['A'], [
            {'material_name':'A','material_render_name':'Renamed','slot_index':1}]), {'A':[1]})

    def test_components_join_exact_seam_duplicates_but_keep_separate_surfaces(self):
        points=[[0,0,0],[1,0,0],[0,1,0],[0,0,0],[1,0,0],[0,-1,0],[4,0,0],[5,0,0],[4,1,0]]
        parts=inspector._components(points,[[0,1,2],[3,4,5],[6,7,8]])
        self.assertEqual(len(parts),2)
        self.assertEqual(sorted(row['triangle_count'] for row in parts),[1,2])

    def test_geometry_digest_changes_with_world_geometry(self):
        self.assertNotEqual(inspector._digest([[0,0,0],[1,0,0],[0,1,0]],[[0,1,2]]),
                            inspector._digest([[0,0,0],[2,0,0],[0,1,0]],[[0,1,2]]))

    def test_single_same_object_slot_is_ordinal_identity_without_name_similarity(self):
        self.assertEqual(inspector._slot_map(['UnrelatedRenderedName'], ['Original'], [], single_slot_identity=True), {'Original':[0]})
        self.assertEqual(inspector._slot_map(['RenamedA','RenamedB'], ['Original'], [], single_slot_identity=True), {'Original':[]})
        self.assertEqual(inspector._slot_map(['Renamed'], ['Original','Other'], [], single_slot_identity=True), {'Original':[],'Other':[]})

    def test_single_slot_fallback_rejects_contradictory_observations(self):
        for observed in ([{'material_name':'Original','material_render_name':'Different','slot_index':0}],
                         [{'material_name':'Original','slot_index':1}],
                         [{'material_name':'Other','slot_index':0}]):
            with self.subTest(observed=observed):
                self.assertEqual(inspector._slot_map(['Rendered'], ['Original'], observed, single_slot_identity=True), {'Original':[]})

    def test_priority_ranks_geometry_only_and_keeps_no_seed_order_stable(self):
        known=[{'name':name} for name in ('Body','Seed','RotatedMatch','WheelNamedButWrongShape','Other')]
        dimensions={'Body':[10,10,10],'Seed':[1,2,.2],'RotatedMatch':[.2,1,2],
                    'WheelNamedButWrongShape':[6,8,9],'Other':[.8,1.7,.3]}
        ordered=inspector._rank_known_objects(known,dimensions,['Seed'])
        self.assertEqual([row['name'] for row in ordered[:2]],['Seed','RotatedMatch'])
        self.assertEqual(inspector._rank_known_objects(known,dimensions,[]),known)

    def test_only_zero_area_triangles_are_degenerate(self):
        self.assertFalse(inspector._nondegenerate([[0,0,0],[0,0,0],[0,0,0]],[0,1,2]))
        self.assertFalse(inspector._nondegenerate([[0,0,0],[1,0,0],[2,0,0]],[0,1,2]))
        self.assertTrue(inspector._nondegenerate([[0,0,0],[1e-10,0,0],[0,1e-10,0]],[0,1,2]))


@unittest.skipUnless(shutil.which('blender'), 'Blender is unavailable: actual scene inspection not run')
class InspectorBlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.addClassCleanup(cls.temp.cleanup)
        builder=cls.root/'build_fixture.py';builder.write_text(FIXTURE,encoding='utf-8')
        cls.run_blender(builder,cls.root)
        cls.fixture=json.loads((cls.root/'fixture.json').read_text())
        cls.source=cls.root/'synthetic_cabinet.blend'
        cls.before=hashlib.sha256(cls.source.read_bytes()).hexdigest()
        out=cls.root/'inspection';out.mkdir()
        request={**{key:cls.fixture[key] for key in ('known_objects','observed_slots')},
                 'blend_path':str(cls.source),'output_dir':str(out),'result_path':str(out/'result.json')}
        path=cls.root/'request.json';path.write_text(json.dumps(request),encoding='utf-8')
        cls.run_blender(SCRIPT,path)
        cls.result=json.loads((out/'result.json').read_text())
        cls.targets={row['object_name']:row for row in cls.result['targets']}

    @classmethod
    def run_blender(cls,script,arg):
        command=[shutil.which('blender'),'-b','--factory-startup','--disable-autoexec','-t','4',
                 '--python-exit-code','1','--python',str(script),'--',str(arg)]
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180)
        if result.returncode:
            raise AssertionError('Real Blender fixture failed:\n'+result.stdout[-5000:])

    def test_actual_inspection_does_not_modify_saved_scene(self):
        self.assertTrue(self.result['success'])
        self.assertTrue(self.result['source_unchanged'])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.before)
        self.assertFalse(self.result['truncated'])
        self.assertEqual(self.result['unresolved'],[])

    def test_actual_hidden_split_wheels_are_present_without_ray_visibility_requirement(self):
        self.assertTrue(self.fixture['occluded_from_hero'])
        self.assertEqual(len(self.fixture['wheel_names']),8)
        for name in self.fixture['wheel_names']:
            target=self.targets[name]
            self.assertEqual(target['material_name'],'Shared')
            self.assertTrue(target['components_complete'])
            self.assertGreater(target['triangle_count'],10)
            self.assertEqual(len(target['thumbnail']['views']),2)
        hidden=set(self.fixture['occluded_from_hero'])
        self.assertTrue(hidden.issubset(self.targets))

    def test_measured_bounds_separate_four_wheels_despite_eight_mesh_names(self):
        # This assertion reads exact measured component bounds.  It does not
        # equate eight mesh names with eight semantic wheels.
        boxes=[self.targets[name]['bounds'] for name in self.fixture['wheel_names']]
        remaining=set(range(len(boxes)));groups=[]
        while remaining:
            group={remaining.pop()};changed=True
            while changed:
                changed=False
                for index in list(remaining):
                    if any(all(boxes[index]['min'][axis]<=boxes[other]['max'][axis]+1e-6 and
                               boxes[other]['min'][axis]<=boxes[index]['max'][axis]+1e-6
                               for axis in range(3)) for other in group):
                        remaining.remove(index);group.add(index);changed=True
            groups.append(group)
        self.assertEqual(sorted(len(group) for group in groups),[2,2,2,2])

    def test_disconnected_surface_and_slotless_identity_are_complete(self):
        self.assertEqual(len(self.targets['Mesh.Disconnected']['components']),2)
        self.assertEqual(self.targets['Mesh.Slotless']['material_name'],'Default material')

    def test_hidden_renamed_single_slots_use_local_identity_and_observed_aliases_remain_distinct(self):
        self.assertEqual(self.targets['Mesh.009']['mapping_basis'],'single_slot_identity')
        self.assertEqual(self.targets['Mesh.010']['mapping_basis'],'single_slot_identity')
        self.assertEqual(self.targets['Mesh.007']['mapping_basis'],'observed_slot_identity')
        self.assertEqual(self.targets['Mesh.003']['mapping_basis'],'exact_material_name')

    def test_render_only_array_modifier_contributes_actual_disconnected_components(self):
        self.assertEqual(len(self.targets['Mesh.RenderArray']['components']),2)

    def test_real_priority_enters_bounded_catalog_and_degenerate_slot_does_not_abort(self):
        folder=self.root/'priority_case';folder.mkdir()
        builder=folder/'add_degenerate.py'
        builder.write_text('''import bpy,sys\nfrom pathlib import Path\nfolder=Path(sys.argv[sys.argv.index('--')+1])\nbpy.ops.wm.open_mainfile(filepath=str(folder.parent/'synthetic_cabinet.blend'),load_ui=False,use_scripts=False)\nmesh=bpy.data.meshes.new('Degenerate');mesh.from_pydata([(0,0,0),(1,0,0),(2,0,0)],[],[(0,1,2)]);mesh.update()\nobj=bpy.data.objects.new('Mesh.Degenerate',mesh);bpy.context.scene.collection.objects.link(obj);mesh.materials.append(bpy.data.materials['Shared'])\nbpy.ops.wm.save_as_mainfile(filepath=str(folder/'source.blend'))\n''',encoding='utf-8')
        self.run_blender(builder,folder)
        output=folder/'inspection';output.mkdir()
        request={**{key:self.fixture[key] for key in ('known_objects','observed_slots')},
                 'blend_path':str(folder/'source.blend'),'output_dir':str(output),'result_path':str(output/'result.json'),
                 'priority_objects':['Mesh.Degenerate','Mesh.009'],'limits':{'max_targets':2}}
        request['known_objects']=request['known_objects']+[{'name':'Mesh.Degenerate','type':'MESH','materials':['Shared']}]
        path=folder/'request.json';path.write_text(json.dumps(request),encoding='utf-8')
        self.run_blender(SCRIPT,path)
        result=json.loads((output/'result.json').read_text())
        self.assertTrue(result['success']);self.assertTrue(result['truncated'])
        self.assertEqual(len(result['targets']),2)
        self.assertEqual(result['targets'][0]['object_name'],'Mesh.009')
        self.assertTrue(all(row['object_name'] in self.fixture['wheel_names'] for row in result['targets']))
        rejected=next(row for row in result['unresolved'] if row['object_name']=='Mesh.Degenerate')
        self.assertIn(rejected['reason'],('degenerate_surface','empty_surface'))

    def test_real_catalog_pages_have_nonempty_geometry_in_both_view_rectangles(self):
        for target in self.targets.values():
            thumbnail=target['thumbnail'];page=self.result['pages'][thumbnail['page']]
            with Image.open(page['path']) as image:
                self.assertEqual(image.size,(1600,1200))
                for view in thumbnail['views']:
                    x,y,w,h=view['rect']
                    crop=image.crop((round(x*1600),round(y*1200),round((x+w)*1600),round((y+h)*1200))).convert('RGB')
                    dark=sum(1 for pixel in crop.getdata() if min(pixel)<200)
                    self.assertGreater(dark,5,target['object_name'])


if __name__ == '__main__':
    unittest.main()

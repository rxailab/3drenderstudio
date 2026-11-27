"""Regression coverage for local catalog replacement on real Blender data."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BLENDER = os.environ.get('BLENDER_EXE') or shutil.which('blender')
FIXTURE = '''
import sys
from pathlib import Path
import bpy
repo, directory, asset_mode = sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0, repo)
if asset_mode=='index-only':
    import keyshot_catalog, shutil
    shutil.copyfile(keyshot_catalog.ROOT/'catalog.json',Path(directory)/'catalog.json')
    keyshot_catalog.ROOT=Path(directory)
from keyshot_catalog import catalog, render_spec
from blender import worker, hero_scene
scene = bpy.context.scene
body = bpy.data.objects['Cube']
body.name = 'Body'
other = body.copy()
other.name = 'Neighbor'
scene.collection.objects.link(other)
original = body.material_slots[0].material
body.data.materials.append(original)
records = {row['name']:row for row in catalog()['materials']}
ids = [records[name]['id'] for name in ('Walnut','Anodized Aluminum Brushed 90° Orange',
       'Gem Stone Amethyst','RealCloth Basket Twill Blue','Urban Concrete')]
names = [body.name,other.name]
def state():
    return hero_scene._fingerprints([scene.objects[n] for n in names])
def row(ident):
    return {'catalog_id':ident,'object_name':'Body','material_name':original.name,'preserve_original':False}
def plan(rows):
    return {'object_material_overrides':rows}
before = state()
previous = plan([])
for ident in ids:
    this = plan([row(ident)])
    allowed = []
    hero_scene._changed_materials(scene,[body,other],this,previous,[],allowed)
    mat = body.material_slots[0].material
    assert mat==body.material_slots[1].material and len(allowed[0]['bindings'])==2
    assert mat['wuying_catalog_id']==ident and other.material_slots[0].material==original
    assert hero_scene._proof(before,state(),allowed)['untouched_materials_verified']
    if ident in (ids[0], ids[3], ids[4]):
        packed = any(getattr(n,'image',None) and n.image.packed_file for n in mat.node_tree.nodes)
        spec = render_spec(ident)
        assert packed == bool(spec['textures'] or spec.get('procedural',{}).get('weave')), ident
    if ident==ids[1]:
        assert mat.diffuse_color[0] > mat.diffuse_color[2]*2 and mat.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value==1
    if ident==ids[2]:
        assert mat.node_tree.nodes.get('Principled BSDF').inputs['Transmission Weight'].default_value==1
    previous = this
target = Path(directory)/'saved.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(target),check_existing=False)
bpy.ops.wm.open_mainfile(filepath=str(target),load_ui=False,use_scripts=False)
scene = bpy.context.scene
body, other = (scene.objects[n] for n in names)
allowed=[]
hero_scene._changed_materials(scene,[body,other],plan([]),previous,[],allowed)
assert body.material_slots[0].material==other.material_slots[0].material
assert body.material_slots[0].link=='DATA'
assert state()['resources_sha256']==before['resources_sha256']
assert state()['colors_sha256']==before['colors_sha256']
bpy.ops.mesh.primitive_cube_add()
no_slot=bpy.context.object
no_slot.data.materials.clear()
original_data=no_slot.data
single=plan([{'catalog_id':ids[0],'object_name':no_slot.name,'material_name':'Default material'}])
hero_scene._changed_materials(scene,[no_slot],single,plan([]),[],[])
assert len(no_slot.material_slots)==1
hero_scene._changed_materials(scene,[no_slot],plan([]),single,[],[])
assert len(no_slot.material_slots)==0 and no_slot.data==original_data
ops, warnings = worker.apply_material_overrides([body,other],plan([{**previous['object_material_overrides'][0],
    'color':'#ff0000','roughness':.78}]))
mat=body.material_slots[0].material
bs=mat.node_tree.nodes.get('Principled BSDF')
assert not bs.inputs['Base Color'].is_linked and list(bs.inputs['Base Color'].default_value)==[1,0,0,1]
assert not bs.inputs['Roughness'].is_linked and abs(bs.inputs['Roughness'].default_value-.78)<.0001
available = render_spec(ids[4])['textures']
assert bool(set(available) - {'base_color','roughness'}) == any(getattr(n,'image',None) and n.image.packed_file for n in mat.node_tree.nodes)
from blender.catalog_shader import apply
external=available.get('base_color')
if not external:
    # External user images must also work in an installation with only public presets.
    external=str(Path(directory)/'transmission.png')
    test_image=bpy.data.images.new('transmission',width=2,height=2)
    test_image.filepath_raw=external
    test_image.file_format='PNG'
    test_image.save()
apply(mat,{'catalog_id':ids[0],'textures':{'transmission':external},'texture_scale':4}, {}, True)
bs=mat.node_tree.nodes.get('Principled BSDF')
walnut_maps=render_spec(ids[0])['textures']
assert bs.inputs['Transmission Weight'].is_linked
assert bs.inputs['Base Color'].is_linked == ('base_color' in walnut_maps)
assert sorted(n.inputs['Scale'].default_value[0] for n in mat.node_tree.nodes if n.type=='MAPPING')==([1.,4.] if walnut_maps else [4.])
scene.render.engine='CYCLES'
scene.cycles.samples=1
scene.cycles.device='CPU'
scene.render.resolution_percentage=100
scene.render.resolution_x=64
scene.render.resolution_y=64
scene.render.filepath=str(Path(directory)/'frame.png')
bpy.ops.render.render(write_still=True)
assert (Path(directory)/'frame.png').stat().st_size>100
'''


@unittest.skipUnless(BLENDER, 'Blender unavailable')
class CatalogShaderTests(unittest.TestCase):
    def run_fixture(self, fixture, asset_mode='installed'):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory)/'fixture.py'
            script.write_text(fixture, encoding='utf-8')
            result = subprocess.run([BLENDER, '-b','-t','2','--factory-startup','--disable-autoexec',
                                     '--python-exit-code','1','--python',str(script),'--',str(ROOT),directory,asset_mode],
                                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                                    encoding='utf-8',errors='replace',timeout=120)
            self.assertEqual(result.returncode,0,result.stdout[-5000:])

    def test_materials_and_checkpoint_restoration(self):
        self.run_fixture(FIXTURE)

    def test_checkpoint_and_user_images_without_local_assets(self):
        self.run_fixture(FIXTURE,'index-only')

    def test_all_entries_build(self):
        self.run_fixture('''
import bpy,sys
sys.path.insert(0,sys.argv[sys.argv.index('--')+1])
from keyshot_catalog import catalog
from blender.catalog_shader import apply
mat = bpy.data.materials.new('Catalog test')
entries = catalog()['materials']
assert len(entries)==953
for row in entries:
    try:
        apply(mat,{'catalog_id':row['id']},{},True)
    except Exception as error:
        raise AssertionError(row['name']) from error
    assert mat['wuying_catalog_id']==row['id']
''')

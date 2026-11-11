"""Self-authored CC0 acceptance assets. Run with Blender --background --python this_file.

No downloaded model, texture, or trademark is included. All dimensions are metres.
"""
import bpy
import bmesh
import math
import json
import random
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'samples'
OUT.mkdir(exist_ok=True)


def clear():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = 1.0


def material(name, color, roughness=.4, metallic=0., transmission=0.):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node = mat.node_tree.nodes.get('Principled BSDF')
    node.inputs['Base Color'].default_value = (*color, 1)
    node.inputs['Roughness'].default_value = roughness
    node.inputs['Metallic'].default_value = metallic
    node.inputs['Transmission Weight'].default_value = transmission
    node.inputs['IOR'].default_value = 1.46 if transmission else 1.5
    mat.diffuse_color = (*color, 1)
    return mat


def assign(obj, mat):
    obj.data.materials.append(mat)
    return obj


def smooth(obj):
    if obj.type == 'MESH':
        for p in obj.data.polygons:
            p.use_smooth = True


def bevel_box(name, loc, size, mat, radius=.03):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    assign(obj, mat)
    if radius:
        modifier = obj.modifiers.new('Manufactured rounded edges', 'BEVEL')
        modifier.width = min(radius, min(size) * .45)
        modifier.segments = 5
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        modifier = obj.modifiers.new('Weighted corner normals', 'WEIGHTED_NORMAL')
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    return obj


def cylinder(name, loc, radius, depth, mat):
    bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=radius, depth=depth, location=loc)
    obj = bpy.context.object
    obj.name = name
    assign(obj, mat)
    mod = obj.modifiers.new('Edge radius', 'BEVEL')
    mod.width = min(.008, depth / 6)
    mod.segments = 3
    bpy.ops.object.modifier_apply(modifier=mod.name)
    smooth(obj)
    return obj


def lathe(name, profile, mat, segments=96):
    verts = []
    faces = []
    for r, z in profile:
        for i in range(segments):
            a = i / segments * math.tau
            verts.append((r*math.cos(a), r*math.sin(a), z))
    for j in range(len(profile)-1):
        for i in range(segments):
            k = j*segments+i
            kn = j*segments+(i+1)%segments
            faces.append((k, kn, kn+segments, k+segments))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    assign(obj, mat)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=.000001)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.uv.smart_project(island_margin=.03)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False)
    smooth(obj)
    return obj


def text_mesh(body, loc, size, mat):
    curve = bpy.data.curves.new(body, 'FONT')
    curve.body = body
    curve.align_x = 'CENTER'
    curve.size = size
    curve.extrude = .0003
    obj = bpy.data.objects.new('Label_' + body, curve)
    bpy.context.collection.objects.link(obj)
    obj.location = loc
    obj.rotation_euler = (math.pi/2, 0, 0)
    assign(obj, mat)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target='MESH')
    return obj


def uv(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=.02)
    bpy.ops.object.mode_set(mode='OBJECT')


def export(name):
    # The generated assets are at useful tabletop scales and contain UVs.
    for obj in list(bpy.context.scene.objects):
        if obj.type == 'MESH' and not obj.data.uv_layers:
            uv(obj)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.export_scene.gltf(filepath=str(OUT / name), export_format='GLB',
        export_materials='EXPORT', export_yup=True, export_apply=True)
    print('SAMPLE_READY:' + str(OUT / name), flush=True)


def plastic():
    clear()
    teal = material('Matte recycled plastic — teal', (.045, .28, .27), .58)
    cream = material('Soft satin plastic — cream', (.78, .74, .62), .33)
    label = material('Paper label — ivory', (.92, .91, .85), .72)
    ink = material('Printed dark green ink', (.018, .055, .045), .65)
    body = lathe('Bottle body', [(0,0),(.12,0),(.135,.008),(.145,.025),
        (.15,.06),(.15,.43),(.147,.46),(.13,.485),(.085,.51),(.062,.52),
        (.062,.56),(0,.56)], teal)
    cylinder('Pump collar', (0,0,.564), .073,.055,cream)
    cylinder('Pump stem', (0,0,.616), .023,.065,cream)
    head = bevel_box('Pump dispenser', (0,-.03,.653), (.125,.19,.035),cream,.012)
    # A curved paper sticker follows the cylinder; a flat plate previously
    # intersected the bottle center and appeared as two white strips.
    verts=[];faces=[]
    for i in range(33):
        theta=-.68+i/32*1.36
        for z in (.2025,.3975):
            verts.append((.1507*math.sin(theta),-.1507*math.cos(theta),z))
    for i in range(32):
        faces.append((2*i,2*i+2,2*i+3,2*i+1))
    mesh=bpy.data.meshes.new('Curved label substrate')
    mesh.from_pydata(verts,[],faces);mesh.update()
    sticker=bpy.data.objects.new('Label substrate',mesh)
    bpy.context.collection.objects.link(sticker);assign(sticker,label);smooth(sticker)
    for copy,z,size in [('MORA',.337,.039),('DAILY CARE',.299,.012),('500 ml',.242,.014)]:
        text=text_mesh(copy,(0,-.1518,z),size,ink)
        # A large glyph is otherwise capped with long, flat triangles. Moving
        # only their corner vertices onto a cylinder leaves the triangle chords
        # inside the sticker. Subdivide first, then wrap every resulting vertex.
        bm=bmesh.new();bm.from_mesh(text.data)
        bmesh.ops.triangulate(bm,faces=list(bm.faces))
        bmesh.ops.subdivide_edges(bm,edges=list(bm.edges),cuts=4,use_grid_fill=True)
        bm.to_mesh(text.data);bm.free()
        bpy.context.view_layer.update()
        matrix=text.matrix_world.copy();inverse=matrix.inverted()
        for vertex in text.data.vertices:
            world=matrix@vertex.co
            world.y=-math.sqrt(max(.001,.1518**2-world.x**2))+(world.y+.1518)
            vertex.co=inverse@world
    # Explicitly elongated acceptance case (height / width > 3).
    from mathutils import Matrix
    flatten=Matrix.Diagonal((.65,.65,1,1))
    for obj in list(bpy.context.scene.objects):
        obj.matrix_world=flatten@obj.matrix_world
    export('matte-pump-bottle.glb')


def metal():
    clear()
    steel = material('Brushed stainless steel', (.52,.57,.62),.29,1)
    bsdf = steel.node_tree.nodes.get('Principled BSDF')
    if 'Anisotropic IOR Level' in bsdf.inputs:
        bsdf.inputs['Anisotropic IOR Level'].default_value = .65
    outer = bevel_box('Single-mesh utility tray', (0,0,.025), (.47,.29,.05),steel,.022)
    cutter = bevel_box('Temporary cavity', (0,0,.045), (.435,.255,.052),steel,.018)
    bpy.context.view_layer.objects.active = outer
    mod = outer.modifiers.new('Formed steel recess', 'BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)
    uv(outer)
    export('brushed-metal-tray.glb')
    # OBJ variant deliberately starts on its short side. Companion MTL uses a
    # nested relative path; the intended texture is deliberately absent.
    outer.rotation_euler.x = math.pi / 2
    bpy.context.view_layer.update()
    minimum = min((outer.matrix_world @ __import__('mathutils').Vector(v)).z for v in outer.bound_box)
    outer.location.z -= minimum
    folder = OUT / 'obj-missing-texture'
    folder.mkdir(exist_ok=True)
    bpy.ops.wm.obj_export(filepath=str(folder/'sideways-tray.obj'), export_materials=True)
    mtl = folder/'sideways-tray.mtl'
    text = mtl.read_text(encoding='utf-8')
    text += '\nmap_Kd textures/brushed-steel-missing.png\n'
    mtl.write_text(text, encoding='utf-8')
    (folder/'README.txt').write_text('CC0 self-authored fixture. Missing texture is deliberate. Tray is sideways; rotate X by -90 degrees for tabletop placement. OBJ physical scale is unspecified.\n',encoding='utf-8')
    with zipfile.ZipFile(OUT/'sideways-missing-texture.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for file in folder.iterdir():
            archive.write(file,file.name)


def glass():
    clear()
    mat = material('Clear thick glass', (.92,.975,1), .035, 0, 1)
    # Continuous surface includes underside, outer wall, rim, inner wall,
    # and inner base: thickness is actual geometry, not a shader illusion.
    obj = lathe('Thick-walled glass tumbler', [(0,0),(.055,0),(.062,.003),(.065,.009),
        (.067,.025),(.075,.14),(.075,.146),(.073,.15),(.069,.15),(.067,.146),
        (.067,.14),(.059,.027),(.057,.018),(.05,.014),(0,.014)],mat,128)
    export('thick-glass-tumbler.glb')


def curve_path(name, coords, radius, mat):
    curve = bpy.data.curves.new(name,'CURVE')
    curve.dimensions = '3D'
    curve.resolution_u = 24
    curve.bevel_depth = radius
    curve.bevel_resolution = 4
    curve.use_fill_caps = True
    spline = curve.splines.new('BEZIER')
    spline.bezier_points.add(len(coords)-1)
    for pt,co in zip(spline.bezier_points,coords):
        pt.co = co
        pt.handle_left_type = 'AUTO'
        pt.handle_right_type = 'AUTO'
    obj = bpy.data.objects.new(name,curve)
    bpy.context.collection.objects.link(obj)
    assign(obj,mat)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target='MESH')
    return obj


def bag():
    clear()
    canvas = material('Woven canvas — sand',(.52,.43,.3),.86)
    leather = material('Brown leather trim',(.10,.037,.017),.47)
    brass = material('Brushed brass hardware',(.52,.31,.095),.25,.9)
    thread = material('Warm cotton stitching',(.65,.52,.34),.9)
    # Repeating image is self-authored so GLB and OBJ test embedded/relative
    # texture handling without external resource licensing dependencies.
    texdir = OUT/'textures'
    texdir.mkdir(exist_ok=True)
    size=256
    image = bpy.data.images.new('Self-authored woven canvas',width=size,height=size)
    random.seed(10)
    pixels=[]
    for y in range(size):
        for x in range(size):
            weave = .89 + .06*math.sin(x*math.pi/4) + .04*math.sin(y*math.pi/4)
            weave += random.uniform(-.022,.022)
            pixels.extend((.66*weave,.57*weave,.42*weave,1))
    image.pixels=pixels
    image.filepath_raw=str(texdir/'canvas-weave.png')
    image.file_format='PNG'
    image.save()
    nodes=canvas.node_tree.nodes
    tex=nodes.new('ShaderNodeTexImage')
    tex.image=image
    canvas.node_tree.links.new(tex.outputs['Color'],nodes.get('Principled BSDF').inputs['Base Color'])
    body = bevel_box('Canvas body', (0,0,.145), (.40,.15,.29),canvas,.033)
    uv(body)
    bevel_box('Leather base', (0,0,.023), (.405,.155,.046),leather,.018)
    bevel_box('Leather top binding', (0,0,.279), (.40,.151,.025),leather,.010)
    for y in [-.079,.079]:
        for x in [-.145,.145]:
            bevel_box('Leather handle anchor', (x,y,.25),(.032,.012,.07),leather,.006)
            rivet=cylinder('Brass handle rivet',(x,y+(-.009 if y<0 else .009),.25),.006,.004,brass)
            rivet.rotation_euler.x=math.pi/2
        curve_path('Leather rolled handle', [(-.145,y,.279),(-.13,y,.43),(0,y,.465),(.13,y,.43),(.145,y,.279)],.011,leather)
    bevel_box('Front leather patch',(0,-.079,.185),(.09,.008,.045),leather,.006)
    text_mesh('MORA',(0,-.084,.179),.016,thread)
    # Sewn front piping plus discrete stitching makes detail shots meaningful.
    curve_path('Canvas piping',[(-.17,-.076,.055),(-.181,-.076,.10),(-.181,-.076,.245),(-.16,-.076,.261),(0,-.076,.261),(.16,-.076,.261),(.181,-.076,.245),(.181,-.076,.10),(.17,-.076,.055)],.002,thread)
    for i in range(27):
        x=-.16+i*.0123
        bevel_box('Base seam stitch',(x,-.081,.047),(.006,.0015,.0015),thread,.0006)
    # A named parent establishes actual hierarchy for state editor inspection.
    root=bpy.data.objects.new('Tote bag assembly',None)
    bpy.context.collection.objects.link(root)
    for obj in list(bpy.context.scene.objects):
        if obj != root:
            obj.parent=root
    export('canvas-leather-tote.glb')
    objdir=OUT/'obj-with-resources'
    (objdir/'textures').mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.obj_export(filepath=str(objdir/'tote.obj'), export_materials=True, path_mode='RELATIVE')
    import shutil
    shutil.copyfile(texdir/'canvas-weave.png',objdir/'textures'/'canvas-weave.png')
    mtl=objdir/'tote.mtl'
    data=mtl.read_text(encoding='utf-8')
    data=data.replace('../textures/canvas-weave.png','textures/canvas-weave.png')
    data=data.replace('..\\textures\\canvas-weave.png','textures/canvas-weave.png')
    mtl.write_text(data,encoding='utf-8')
    with zipfile.ZipFile(OUT/'canvas-tote-resources.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for file in objdir.rglob('*'):
            if file.is_file(): archive.write(file,file.relative_to(objdir))


plastic()
metal()
glass()
bag()
index=[
    {'id':'matte-bottle','name':'哑光塑料按压瓶','description':'细长物体，哑光塑料、独立泵头、已有标签文字；GLB。','file':'matte-pump-bottle.glb','license':'CC0-1.0','features':['matte plastic','elongated','multipart','text label']},
    {'id':'metal-tray','name':'拉丝金属收纳盘','description':'扁平单网格金属物体，圆角与真实凹槽；GLB。','file':'brushed-metal-tray.glb','license':'CC0-1.0','features':['metal','flat','single mesh','concave']},
    {'id':'glass-tumbler','name':'厚壁透明玻璃杯','description':'真实内外壁、杯口及杯底厚度，单网格；GLB。','file':'thick-glass-tumbler.glb','license':'CC0-1.0','features':['transparent','hollow','single mesh','actual thickness']},
    {'id':'canvas-tote','name':'帆布皮革手提包','description':'帆布纹理、皮革、金属铆钉与缝线，多部件层级；GLB。','file':'canvas-leather-tote.glb','license':'CC0-1.0','features':['fabric','leather','mixed materials','multipart','embedded texture','hierarchy']},
    {'id':'obj-resources','name':'带贴图资源的 OBJ 手提包','description':'OBJ、MTL 和 textures 子目录，验证相对贴图路径；ZIP。','file':'canvas-tote-resources.zip','license':'CC0-1.0','features':['OBJ','MTL','relative texture paths']},
    {'id':'missing-sideways','name':'侧放且缺贴图的金属盘','description':'故意缺失 MTL 贴图且侧向摆放，验证警告、回退与旋转修正；ZIP。','file':'sideways-missing-texture.zip','license':'CC0-1.0','features':['OBJ','missing texture','wrong orientation']}
]
(OUT/'index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'LICENSE.txt').write_text('SPDX-License-Identifier: CC0-1.0\n\nAll models, geometry, fictional MORA labels, materials and textures in this directory were procedurally authored for this application. The authors dedicate them to the public domain under CC0 1.0 Universal. No third-party assets or real brand marks are used.\nhttps://creativecommons.org/publicdomain/zero/1.0/\n',encoding='utf-8')
(OUT/'README.md').write_text('''# 验收样例资产

全部资产由 `scripts/generate_samples.py` 在 Blender 中实际建模并导出，许可为 CC0-1.0。MORA 为虚构样例标签。样例几何是人工规则生成的测试产品，不声称是扫描实物。

| 文件 | 验证内容 |
| --- | --- |
| matte-pump-bottle.glb | 细长哑光塑料、多部件、保留文字 |
| brushed-metal-tray.glb | 扁平金属、单网格、凹槽 |
| thick-glass-tumbler.glb | 透明材料、空心、真实壁厚 |
| canvas-leather-tote.glb | 帆布、皮革、金属、纹理、缝线、层级 |
| canvas-tote-resources.zip | OBJ、MTL、子目录相对纹理路径 |
| sideways-missing-texture.zip | 故意缺失贴图、朝向待修正；建议 X 轴 -90°，具体以导入轴转换后的预览为准 |

这些模型可以通过应用的样例入口或正常上传入口导入；ZIP 保留资源目录结构。GLB 单位依 glTF 规范为米，OBJ 没有可靠实际单位。自动按可摄影尺度归一化不会写回原文件。

材质中的金属各向异性可能因 glTF 导出器能力丢失。需要方向性拉丝时，在应用材质编辑中选择内置拉丝金属并调节方向；不可将仅金属度与粗糙度误称为已验证拉丝方向。

本目录只提供真实模型资产。渲染是否通过以应用输出和验收报告为准。
''',encoding='utf-8')
print('ALL_SAMPLES_COMPLETE',flush=True)

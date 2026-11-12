"""Render a product-only coverage matte from the scene saved with a rendered shot.

The request names the exact .blend written beside a rendered image. This script
opens it read-only, keeps only the registered product meshes, replaces their
shading with flat emission and renders the same camera on transparent film. The
alpha channel of the PNG it writes is the product's pixel coverage in that shot:
the application uses it to produce pure-white and transparent e-commerce images
without re-rendering the product. Nothing is saved back to the source scene.
"""
import json
import sys
from pathlib import Path

import bpy

RENDERABLE = {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'VOLUME', 'POINTCLOUD', 'CURVES', 'GREASEPENCIL'}


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def select_device(scene):
    """Prefer an available GPU; flat emission also renders quickly on CPU."""
    preferences = bpy.context.preferences.addons['cycles'].preferences
    for api in ('OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL'):
        try:
            preferences.compute_device_type = api
            preferences.get_devices()
            if any(device.type == api for device in preferences.devices):
                for device in preferences.devices:
                    device.use = device.type == api
                scene.cycles.device = 'GPU'
                return api
        except Exception:
            continue
    scene.cycles.device = 'CPU'
    return 'CPU'


def render(request):
    source = Path(request['blend_path']).resolve()
    target = Path(request['matte_path']).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    names = scene.get('studio_product_objects')
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except ValueError:
            names = None
    if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names):
        raise ValueError('保存的场景没有登记产品部件，无法生成产品蒙版')
    registered = set(names)
    products = [obj for obj in scene.objects if obj.type == 'MESH' and obj.name in registered and not obj.hide_render]
    if not products:
        raise ValueError('保存的场景中没有可见的产品网格，无法生成产品蒙版')
    if scene.camera is None or scene.camera.type != 'CAMERA':
        raise ValueError('保存的场景缺少出图相机，无法生成产品蒙版')
    # Studio floor, reflection cards and negative fill never belong to the product.
    for obj in scene.objects:
        if obj.type in RENDERABLE and obj not in products:
            obj.hide_render = True
    matte = bpy.data.materials.new('Wuying product matte')
    matte.use_nodes = True
    nodes = matte.node_tree.nodes
    nodes.clear()
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = (1, 1, 1, 1)
    emission.inputs['Strength'].default_value = 1
    output = nodes.new('ShaderNodeOutputMaterial')
    matte.node_tree.links.new(emission.outputs['Emission'], output.inputs['Surface'])
    bpy.context.view_layer.material_override = matte
    render_settings = scene.render
    render_settings.engine = 'CYCLES'
    if request.get('device') == 'CPU':
        scene.cycles.device = 'CPU'
        device = 'CPU'
    else:
        device = select_device(scene)
    scene.cycles.samples = max(4, min(64, int(request.get('samples', 16))))
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.use_denoising = False
    scene.cycles.max_bounces = 0
    scene.cycles.time_limit = 0
    render_settings.film_transparent = True
    render_settings.use_border = False
    render_settings.use_motion_blur = False
    render_settings.use_compositing = False
    render_settings.use_sequencer = False
    render_settings.resolution_percentage = 100
    render_settings.image_settings.file_format = 'PNG'
    render_settings.image_settings.color_mode = 'RGBA'
    render_settings.image_settings.color_depth = '8'
    scene.view_settings.view_transform = 'Standard'
    try:
        scene.view_settings.look = 'None'
    except TypeError:
        pass
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    expected = [render_settings.resolution_x, render_settings.resolution_y]
    wanted = request.get('size')
    if wanted and list(wanted) != expected:
        raise ValueError('保存的场景分辨率与图片不一致，无法对齐产品蒙版')
    target.parent.mkdir(parents=True, exist_ok=True)
    render_settings.filepath = str(target)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(target), check_existing=False)
    try:
        if list(image.size) != expected:
            raise RuntimeError('产品蒙版尺寸验证失败')
    finally:
        bpy.data.images.remove(image)
    return {'success': True, 'width': expected[0], 'height': expected[1], 'device': device,
            'samples': scene.cycles.samples, 'products': [obj.name for obj in products],
            'blender_version': bpy.app.version_string}


def main():
    request = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
    result_path = Path(request['result_path'])
    try:
        result = render(request)
    except Exception as exc:
        write(result_path, {'success': False, 'error': str(exc)})
        raise
    write(result_path, result)


if __name__ == '__main__':
    main()

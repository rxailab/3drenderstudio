"""The product's own model set down in a photographed place (a backdrop), at its real size, lit by that place.

Run by scene_backdrop.py:   blender -b --factory-startup --python blender/backdrop.py -- request.json

A backdrop is a picture of an empty place and what was measured in it once (scripts/backdrop_measure.py): the
camera's focal length, the plane things stand on and the camera's height above it, which pixels of that plane are
free, a coarse depth map and the lamps in view. Here the product is imported and prepared exactly as for every other
render (worker.prepare: orientation, materials, moving parts), scaled to its real size and stood on the free plane
where it fills the asked share of the frame. No image model draws anything, so the product cannot change, cannot be
the wrong size and cannot balance on something it would fall off. A product too small to fill that share anywhere in
the frame is shown in a part of the photograph instead (a square window around it, enlarged at most MAX_ZOOM times):
the camera stays where it was, only its field of view narrows, so the geometry and the light are the same.

Light. What the camera saw is put back as a glowing shell (the picture on its own depth); the half of the room behind
the camera glows in the room's average colour; lamps in view shine from where they are; a soft panel stands in for the
ceiling out of view. Their strengths are solved so that the rendered empty plane is as bright as the photographed one.

Composite. What the product does to the plane (its shadow, the colour it throws back) is the rendered plane with the
product divided by the rendered plane without it. The photograph is multiplied by that and the product laid on top,
as soft and as grainy as the photograph.

request.json: model_path, plan, longest_m (the product's longest side at rest, metres), backdrop (folder), output_dir,
azimuth (degrees, as in a shot), share (longer side of the product in the frame), share_range, stand ('floor' or
'surface'), samples, long (pixels; the backdrop's own size when absent), force_cpu.
result.json: success, file, box, share, ... or success False with reason 'no_fit' (the product has no free place in
this backdrop at an acceptable size) or 'error'.

World axes: the origin is where the product stands, Z is the plane's normal, Y points away from the camera.
Camera axes in the measurements are OpenCV's: x right, y down, z forward.
"""
import json
import math
import sys
import time
import traceback
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blender import worker                       # the app's own import, normalisation, materials and poses

LUMA = np.array([.2126, .7152, .0722])
WHITE = .8                    # what the brightest matt surfaces of a room reflect: the plane's own reflectance is read against them
LAMP_SHARE = .25              # of the light reaching the plane: from the lamps in view
CEILING_M = {'floor': 2.5, 'surface': 1.7}       # above the plane, when no ceiling lamp in view gives the height
PANEL_M = {'floor': 4.0, 'surface': 1.6}         # the unseen ceiling's light, a square this wide over product and camera
LEAN = .7                     # of the ceiling's height: how far that light stands off to the side the plane is brighter on
MARGIN = .04                  # of the frame, kept clear around the product
OCCLUDED = .02                # share of the product's box that may lie behind something in the photograph
SOFTEN = .7                   # pixels at 2048: the product is blurred this much, as sharp as a generated photograph
MAX_ZOOM = 2.0                # a product too small for the whole frame is shown in a part of the photograph, enlarged up to this much
CALIBRATION_PX = 256


class Unfit(Exception):
    """The product has no free place in this backdrop at an acceptable size."""


def linear(value):
    value = np.asarray(value, dtype=np.float32)
    return np.where(value <= .04045, value / 12.92, ((np.maximum(value, 0) + .055) / 1.055) ** 2.4)


def display(value):
    value = np.maximum(np.asarray(value, dtype=np.float32), 0)
    return np.where(value <= .0031308, value * 12.92, 1.055 * value ** (1 / 2.4) - .055)


def picture(path, channels=3):
    """An image file as rows from the top: (image, values, is_float). Eight-bit files give display values, float files scene values."""
    image = bpy.data.images.load(str(path)); w, h = image.size
    values = np.empty(w * h * 4, dtype=np.float32); image.pixels.foreach_get(values)
    return image, values.reshape(h, w, 4)[::-1, :, :channels].copy(), bool(image.is_float)


def blur(array, sigma):
    if sigma < .3:
        return array
    radius = max(1, int(sigma * 3 + .5)); x = np.arange(-radius, radius + 1); kernel = np.exp(-x * x / (2 * sigma * sigma)); kernel /= kernel.sum()
    for axis in (0, 1):
        pad = [(0, 0)] * array.ndim; pad[axis] = (radius, radius)
        padded = np.pad(array, pad, mode='edge'); total = np.zeros_like(array, dtype=np.float32)
        for index, weight in enumerate(kernel):
            window = [slice(None)] * array.ndim; window[axis] = slice(index, index + array.shape[axis])
            total += np.float32(weight) * padded[tuple(window)]
        array = total
    return array


def erode(mask, radius):
    padded = np.pad(mask, radius, mode='edge'); out = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            out = np.minimum(out, padded[radius + dy:radius + dy + mask.shape[0], radius + dx:radius + dx + mask.shape[1]])
    return out


def window(array, x0, y0, side, h, w, smooth):
    """The square part of an array from (x0, y0), ``side`` of its width wide (all as shares of the array), at h by w pixels."""
    high, wide = array.shape[:2]
    ys = (y0 + (np.arange(h) + .5) * side / h) * high - .5; xs = (x0 + (np.arange(w) + .5) * side / w) * wide - .5
    if not smooth:
        return array[np.clip(np.rint(ys), 0, high - 1).astype(int)][:, np.clip(np.rint(xs), 0, wide - 1).astype(int)]
    top = np.clip(np.floor(ys), 0, high - 1).astype(int); below = np.minimum(top + 1, high - 1); fy = np.clip(ys - np.floor(ys), 0, 1).astype(np.float32)
    left = np.clip(np.floor(xs), 0, wide - 1).astype(int); right = np.minimum(left + 1, wide - 1); fx = np.clip(xs - np.floor(xs), 0, 1).astype(np.float32)
    fy = fy.reshape(-1, 1, *([1] * (array.ndim - 2))); fx = fx.reshape(1, -1, *([1] * (array.ndim - 2)))
    upper = array[top][:, left] * (1 - fx) + array[top][:, right] * fx
    lower = array[below][:, left] * (1 - fx) + array[below][:, right] * fx
    return (upper * (1 - fy) + lower * fy).astype(np.float32)


def insert(req, out):
    started = time.time(); scene = bpy.context.scene
    folder = Path(req['backdrop']); geo = json.loads((folder / 'geometry.json').read_text(encoding='utf-8'))
    W, H = geo['size']; focal = float(geo['focal_px']); height = float(geo['height_m'])
    normal = np.array(geo['normal'], dtype=np.float64); normal /= np.linalg.norm(normal)
    step = int(geo.get('depth_step', 8)); depth = np.load(folder / 'depth.npy').astype(np.float32)
    stand = req.get('stand') if req.get('stand') in CEILING_M else 'floor'
    report = {'backdrop': folder.name, 'stand': stand}

    # ------------------------------------------------------------ the product, as the app prepares it
    objects, meshes = worker.load_asset(req['model_path'])
    worker.prepare(objects, meshes, req['plan'])
    root = bpy.data.objects['Studio asset transform']
    place = bpy.data.objects.new('Placement', None); scene.collection.objects.link(place)
    root.parent = place
    place.scale = (float(req['longest_m']) / 2,) * 3            # prepare() made the longest side two units
    bpy.context.view_layer.update()
    product = [ob for ob in scene.objects if ob.type == 'MESH' and len(ob.data.polygons)]
    _, _, corners = worker.bounds(product)
    at_rest = np.array([list(corner) for corner in corners], dtype=np.float64)          # metres, before it is turned

    # ------------------------------------------------------------ the photograph and its camera
    background, shown, floated = picture(folder / 'backdrop.png')
    if floated: shown = display(shown)
    mask_image, support, _ = picture(folder / 'support.png', 1); support = support[..., 0] > .5
    bpy.data.images.remove(mask_image)
    z_axis = normal; x_axis = np.array([1.0, 0, 0]); x_axis = x_axis - (x_axis @ z_axis) * z_axis; x_axis /= np.linalg.norm(x_axis)
    turn = np.vstack([x_axis, np.cross(z_axis, x_axis), z_axis])                           # world from camera

    def spot_at(u, v):
        ray = np.array([(u - W / 2) / focal, (v - H / 2) / focal, 1.0]); down = normal @ ray
        return None if down > -1e-4 else ray * (-height / down)                            # above the horizon: no plane there

    def seen(points, spot):
        cam = points @ turn + spot
        return focal * cam[:, 0] / cam[:, 2] + W / 2, focal * cam[:, 1] / cam[:, 2] + H / 2, cam[:, 2]

    def turned(points, yaw):
        c, s = math.cos(yaw), math.sin(yaw)
        return points @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]).T

    target = float(req.get('share', .5)); low, high = req.get('share_range') or (.3, .64)
    most = float(np.clip(req.get('max_zoom', MAX_ZOOM), 1, 4))

    def stand_it(yaw):
        """Where on the free plane the product is closest to the asked size, nothing of the photograph on or before it;
        and the square window of the frame that shows it so: (u, v, spot, share, zoom, window x0, y0, side)."""
        points = turned(at_rest, yaw); foot = points.copy(); foot[:, 2] = 0; foot = np.vstack([foot, foot.mean(0)])
        best = None; largest = 0.0
        for u in np.linspace(.12 * W, .88 * W, 39):
            for v in np.linspace(.4 * H, .995 * H, 110):
                spot = spot_at(u, v)
                if spot is None: continue
                px, py, pz = seen(points, spot)
                if pz.min() < .05: continue
                x0, x1, y0, y1 = px.min() / W, px.max() / W, py.min() / H, py.max() / H
                if x0 < 0 or y0 < 0 or x1 > 1 or y1 > 1: continue
                whole = max(x1 - x0, y1 - y0)
                # enlarged no more than it takes to show the product at a fair size: the place around it is the point of the picture
                aim = target if whole >= target else min(target, low + .1)
                zoom = float(np.clip(aim / max(whole, 1e-6), 1, most)); share = whole * zoom; side = 1 / zoom
                lowered = .56 + .12 * (zoom - 1)                 # in an enlarged view the product sits lower: more of the place behind it
                largest = max(largest, whole * most)
                if not low <= share <= high: continue
                wx = float(np.clip((x0 + x1) / 2 - .5 * side, 0, 1 - side)); wy = float(np.clip((y0 + y1) / 2 - lowered * side, 0, 1 - side))
                if min(x0 - wx, y0 - wy, wx + side - x1, wy + side - y1) < MARGIN * side: continue        # not wholly in the picture
                fx, fy, _ = seen(foot, spot); fx = fx.astype(int); fy = fy.astype(int)
                if fx.min() < 0 or fy.min() < 0 or fx.max() >= W or fy.max() >= H or not support[fy, fx].all(): continue
                around = depth[int(y0 * H / step):int(y1 * H / step) + 1, int(x0 * W / step):int(x1 * W / step) + 1]
                if around.size and float((around < pz.min() * .97).mean()) > OCCLUDED: continue
                centred = abs(((x0 + x1) / 2 - wx) / side - .5) + .5 * abs(((y0 + y1) / 2 - wy) / side - lowered)
                score = abs(share - aim) + .15 * (zoom - 1) + .3 * centred + .1 * abs((x0 + x1) / 2 - .5)
                if best is None or score < best[0]: best = (score, u, v, spot, share, zoom, wx, wy, side)
        if best is None:
            raise Unfit('largest free placement fills %.2f of the picture; %.2f to %.2f is asked' % (largest, low, high))
        return best[1:]

    azimuth = math.radians(float(req.get('azimuth', 25)))
    yaw = -azimuth
    for _ in range(2):                                           # the camera does not stand straight in front: turn the product to the asked side
        spot = stand_it(yaw)[2]
        eye = -turn @ spot; yaw = math.atan2(eye[0], -eye[1]) - azimuth
    u, v, spot, share, zoom, wx, wy, side = stand_it(yaw)
    eye = -turn @ spot
    place.rotation_euler = (0, 0, yaw); bpy.context.view_layer.update()
    report.update(base_pixel=[round(float(u)), round(float(v))], distance_m=round(float(np.linalg.norm(spot)), 3), share=round(float(share), 3),
                  zoom=round(zoom, 3), window=[round(wx, 4), round(wy, 4), round(side, 4)],
                  yaw_deg=round(math.degrees(yaw), 1), product_m=[round(float(n), 3) for n in np.ptp(turned(at_rest, yaw), axis=0)])

    camera_data = bpy.data.cameras.new('Photograph'); camera = bpy.data.objects.new('Photograph', camera_data)
    scene.collection.objects.link(camera); scene.camera = camera
    # the window of the frame: the same camera with a longer lens, its view shifted to the window's middle
    camera_data.sensor_fit = 'HORIZONTAL'; camera_data.sensor_width = 36; camera_data.lens = focal * 36 / W * zoom
    camera_data.shift_x = (wx + side / 2 - .5) / side; camera_data.shift_y = -(wy + side / 2 - .5) / side * H / W
    camera_data.clip_start = .02; camera_data.clip_end = 300
    matrix = Matrix.Identity(4)
    for column, axis in enumerate(((1, 0, 0), (0, -1, 0), (0, 0, -1))):                  # Blender's camera: x right, y up, looking down -z
        for row, value in enumerate(turn @ np.array(axis, dtype=float)): matrix[row][column] = value
    for row, value in enumerate(eye): matrix[row][3] = value
    camera.matrix_world = matrix
    long = int(req.get('long') or W); width = long; tall = round(long * H / W)

    def to_world(points):
        return (np.asarray(points, dtype=np.float64) - spot) @ turn.T

    # ------------------------------------------------------------ the room in the photograph, as light
    rows, cols = np.mgrid[0:depth.shape[0], 0:depth.shape[1]]; pu = cols * step; pv = rows * step
    cloud = np.dstack([(pu - W / 2) / focal * depth, (pv - H / 2) / focal * depth, depth]).reshape(-1, 3)
    index = np.arange(rows.size).reshape(rows.shape)
    quads = np.stack([index[:-1, :-1], index[1:, :-1], index[1:, 1:], index[:-1, 1:]], -1).reshape(-1, 4)
    zq = depth.reshape(-1)[quads]; torn = (zq.max(1) - zq.min(1)) / np.maximum(zq.min(1), 1e-3) > .06      # a depth edge, not a surface
    ground = support[np.minimum(pv, H - 1), np.minimum(pu, W - 1)].reshape(-1)[quads].all(1)               # the plane itself is modelled below
    shell = bpy.data.meshes.new('Room'); shell.from_pydata(to_world(cloud).tolist(), [], quads[~torn & ~ground].tolist()); shell.update()
    layer = shell.uv_layers.new(name='picture')
    loops = np.zeros(len(shell.loops), dtype=np.int32); shell.loops.foreach_get('vertex_index', loops)
    layer.data.foreach_set('uv', np.stack([pu.reshape(-1) / W, 1 - pv.reshape(-1) / H], 1)[loops].astype(np.float32).ravel())
    room = bpy.data.objects.new('Room', shell); scene.collection.objects.link(room); room.visible_camera = False
    glowing = bpy.data.materials.new('Room light'); glowing.use_nodes = True; nodes = glowing.node_tree.nodes; nodes.clear()
    texture = nodes.new('ShaderNodeTexImage'); texture.image = background; texture.extension = 'EXTEND'
    glow = nodes.new('ShaderNodeEmission'); surface = nodes.new('ShaderNodeOutputMaterial')
    glowing.node_tree.links.new(texture.outputs['Color'], glow.inputs['Color']); glowing.node_tree.links.new(glow.outputs['Emission'], surface.inputs['Surface'])
    shell.materials.append(glowing)

    light = linear(shown); luminance = light @ LUMA.astype(np.float32)
    world = bpy.data.worlds.new('Unseen room'); scene.world = world; world.use_nodes = True
    sky = world.node_tree.nodes['Background']
    sky.inputs['Color'].default_value = (*[float(n) for n in light[~support].reshape(-1, 3).mean(0)], 1) if (~support).any() else (.5, .5, .5, 1)

    lamps = []; ceiling = CEILING_M[stand]
    for row in (geo.get('lamps') or [])[:2]:
        centre = to_world(row['centre']); along = np.array(row['along']) @ turn.T; across = np.array(row['across']) @ turn.T
        facing = np.cross(along, across); facing /= max(np.linalg.norm(facing), 1e-9)
        if facing @ (np.array([0, 0, .3]) - centre) < 0: facing = -facing                 # towards the product
        data = bpy.data.lights.new('Lamp', 'AREA'); data.shape = 'RECTANGLE'; data.color = (1, 1, 1)
        data.size = float(np.clip(row['size'][0], .2, 6)); data.size_y = float(np.clip(row['size'][1], .08, 6))
        lamp = bpy.data.objects.new('Lamp', data); scene.collection.objects.link(lamp)
        z = -facing; y = np.cross(z, along); y /= max(np.linalg.norm(y), 1e-9); x = np.cross(y, z)     # an area lamp shines down its -Z
        lamp.matrix_world = Matrix([[x[0], y[0], z[0], centre[0]], [x[1], y[1], z[1], centre[1]], [x[2], y[2], z[2], centre[2]], [0, 0, 0, 1]])
        lamps.append((lamp, float(row.get('pixels', 1))))
        if abs(facing[2]) > .8 and centre[2] > 1.0: ceiling = float(centre[2])
    weight = sum(amount for _, amount in lamps) or 1
    panel_data = bpy.data.lights.new('Ceiling', 'AREA'); panel_data.shape = 'SQUARE'; panel_data.size = PANEL_M[stand]; panel_data.color = (1, 1, 1)
    panel = bpy.data.objects.new('Ceiling', panel_data); scene.collection.objects.link(panel)
    # Which side the unseen light is on shows on the plane: it is brighter towards it. A light straight overhead gives a
    # thin thing lying flat no shadow to stand on.
    ys, xs = np.nonzero(support[::16, ::16]); ys = ys * 16; xs = xs * 16; lean = np.zeros(2)
    if len(xs) > 50:
        rays = np.stack([(xs - W / 2) / focal, (ys - H / 2) / focal, np.ones(len(xs))], 1); down = rays @ normal
        hit = down < -1e-4; where = to_world(rays[hit] * (-height / down[hit])[:, None])[:, :2]; tone = luminance[ys[hit], xs[hit]]
        slope, *_ = np.linalg.lstsq(np.column_stack([where, np.ones(len(where))]), tone, rcond=None)
        steep = float(np.linalg.norm(slope[:2])) / max(float(tone.mean()), 1e-4)                # share of brightness gained per metre
        if steep > .03: lean = slope[:2] / np.linalg.norm(slope[:2]) * min(1.0, steep * 4)
    panel.location = (eye[0] / 2 + lean[0] * LEAN * ceiling, eye[1] / 2 + lean[1] * LEAN * ceiling, ceiling - .02)
    aim = Vector((eye[0] / 2, eye[1] / 2, 0)) - panel.location                               # it still faces where the product stands
    panel.rotation_euler = aim.to_track_quat('-Z', 'Y').to_euler()
    report['unseen_light_leans'] = [round(float(n), 2) for n in lean]

    # ------------------------------------------------------------ the plane the product stands on
    bpy.ops.mesh.primitive_plane_add(size=80, location=(0, 0, 0)); plane = bpy.context.object; plane.name = 'Support'
    white = float(np.percentile(luminance[shown.max(2) < .96], 95)) if (shown.max(2) < .96).any() else 1.0
    albedo = float(np.clip(WHITE * float(np.median(luminance[support])) / max(white, 1e-3), .15, .8))
    tone = np.median(light[support], axis=0).astype(np.float64); tone = np.clip(tone / max(float(tone @ LUMA), 1e-4) * albedo, 0, .95)
    paint = bpy.data.materials.new('Support'); paint.use_nodes = True
    bsdf = paint.node_tree.nodes['Principled BSDF']; bsdf.inputs['Base Color'].default_value = (*[float(n) for n in tone], 1); bsdf.inputs['Roughness'].default_value = .6
    plane.data.materials.append(paint)

    # ------------------------------------------------------------ how bright: the rendered empty plane must match the photographed one
    scene.render.engine = 'CYCLES'; device = worker.device(req.get('force_cpu', False))
    scene.view_settings.view_transform = 'Standard'; scene.view_settings.look = 'None'; scene.view_settings.exposure = 0; scene.view_settings.gamma = 1
    scene.cycles.use_denoising = True; scene.cycles.max_bounces = 8
    scene.render.image_settings.file_format = 'OPEN_EXR'; scene.render.image_settings.color_mode = 'RGBA'; scene.render.image_settings.color_depth = '32'

    def shoot(name, w, h, samples, transparent=False):
        scene.render.resolution_x = w; scene.render.resolution_y = h; scene.render.resolution_percentage = 100
        scene.render.film_transparent = transparent; scene.cycles.samples = samples
        path = out / (name + '.exr'); scene.render.filepath = str(path); bpy.ops.render.render(write_still=True)
        image, values, _ = picture(path, 4); bpy.data.images.remove(image); path.unlink(missing_ok=True)
        return values

    half = .07 * side; cx, cy = u / W, v / H                   # the plane around the product's foot, in the whole frame

    def patch(h, w, x0=0.0, y0=0.0, wide=1.0):
        """That piece of plane in a picture h by w that shows the frame from (x0, y0), ``wide`` of it."""
        return (slice(int(np.clip((cy - half - y0) / wide, 0, 1) * h), max(int(np.clip((cy + half - y0) / wide, 0, 1) * h), 1)),
                slice(int(np.clip((cx - half - x0) / wide, 0, 1) * w), max(int(np.clip((cx + half - x0) / wide, 0, 1) * w), 1)))

    def plane_light():
        values = shoot('calibrate', CALIBRATION_PX, round(CALIBRATION_PX * H / W), 96)
        return float((values[patch(*values.shape[:2], wx, wy, side)][..., :3] @ LUMA.astype(np.float32)).mean())

    def lights(sky_strength=0.0, lamp_watts=0.0, panel_watts=0.0):
        sky.inputs['Strength'].default_value = sky_strength; panel.data.energy = panel_watts
        for lamp, amount in lamps: lamp.data.energy = lamp_watts * amount / weight

    for ob in product: ob.hide_render = True
    here = patch(H, W); free = support[here]
    wanted = float(luminance[here][free].mean()) if free.any() else float(np.median(luminance[support]))
    lights(); from_room = plane_light()
    lights(sky_strength=1); from_sky = plane_light() - from_room               # the walls out of view, as bright as the room in view
    lights(lamp_watts=100); from_lamp = (plane_light() - from_room) if lamps else 0.0
    lights(panel_watts=100); from_panel = plane_light() - from_room
    lamp_part = LAMP_SHARE * wanted if from_lamp > 1e-6 else 0.0
    sky_strength = float(np.clip((wanted - from_room - lamp_part) / max(from_sky, 1e-6), .1, 1))
    panel_part = max(wanted - from_room - sky_strength * from_sky - lamp_part, 0.0)
    lamp_watts = float(100 * lamp_part / from_lamp) if lamp_part else 0.0
    panel_watts = float(100 * panel_part / max(from_panel, 1e-6))
    lights(sky_strength, lamp_watts, panel_watts)
    report['light'] = {'photographed_plane': round(wanted, 4), 'plane_reflectance': round(albedo, 3), 'from_room_in_view': round(from_room, 4),
                       'from_walls_out_of_view': round(sky_strength * from_sky, 4), 'from_lamps_in_view': round(lamp_part, 4),
                       'from_ceiling_out_of_view': round(panel_part, 4), 'lamps': len(lamps), 'lamp_watts': round(lamp_watts, 1),
                       'ceiling_watts': round(panel_watts, 1), 'ceiling_m': round(ceiling, 2), 'rendered_plane': round(plane_light(), 4)}

    # ------------------------------------------------------------ three renders, one picture
    samples = int(req.get('samples', 256)); scene.cycles.use_adaptive_sampling = True; scene.cycles.adaptive_threshold = .01
    empty = shoot('plane-empty', width, tall, samples)[..., :3]
    for ob in product: ob.hide_render = False
    with_product = shoot('plane-with', width, tall, samples)[..., :3]
    plane.visible_camera = False                                # the plane still bounces its light up onto the product
    alone = shoot('product', width, tall, samples, transparent=True)             # scene values, colour already multiplied by alpha
    alpha = alone[..., 3]
    if float(alpha.max()) < .5:
        raise Unfit('the product is not in the picture')
    covered = np.argwhere(alpha > .02); (top, left), (bottom, right) = covered.min(0), covered.max(0)

    photograph = window(shown, wx, wy, side, tall, width, True); free = window(support, wx, wy, side, tall, width, False)
    ratio = np.clip(with_product / np.maximum(empty, 1e-4), 0, 1.6)
    valid = ((alpha < .004) & free).astype(np.float32)
    # under the product's edge the ratio is the product's own colour: filled in from the plane around it
    sigma = max(width / 300, 2); reach = int(sigma * 3) + 2
    y0, y1, x0, x1 = max(top - reach, 0), min(bottom + reach + 1, tall), max(left - reach, 0), min(right + reach + 1, width)
    near = ratio[y0:y1, x0:x1]; known = valid[y0:y1, x0:x1]
    spread = blur(near * known[..., None], sigma); amount = blur(known, sigma)[..., None]
    ratio[y0:y1, x0:x1] = np.where(known[..., None] > 0, near, np.where(amount > 1e-3, spread / np.maximum(amount, 1e-3), 1))
    ratio = blur(ratio, width / 1400)                           # the last of the render's noise
    edge = blur(erode(free.astype(np.float32), 2), width / 500)[..., None]       # no shadow painted over what stands on the plane
    plane_now = linear(photograph) * (1 + (ratio - 1) * edge)
    soft = SOFTEN * width / 2048 * math.sqrt(zoom)              # an enlarged part of the photograph is softer
    if soft >= .3: alone = blur(alone, soft)
    result = display(alone[..., :3] + plane_now * (1 - alone[..., 3:4]))
    grey = photograph @ LUMA.astype(np.float32); flat = (grey - blur(grey, 1.5))[free]
    grain = float(np.median(np.abs(flat)) * 1.4826) if flat.size else 0.0         # the photograph's own noise, read on its plane
    if grain > 1e-4:
        result = result + np.random.default_rng(7).normal(0, grain, (tall, width, 1)).astype(np.float32) * alone[..., 3:4]
    result = np.clip(result, 0, 1)

    target_path = out / (out.name + '.png')
    image = bpy.data.images.new('Composite', width, tall, alpha=False, float_buffer=False)
    image.pixels.foreach_set(np.concatenate([result[::-1], np.ones((tall, width, 1), np.float32)], 2).astype(np.float32).ravel())
    image.filepath_raw = str(target_path); image.file_format = 'PNG'; image.save()
    report.update(success=True, file=target_path.name, size=[width, tall],
                  box=[round(float(left) / width, 4), round(float(top) / tall, 4), round(float(right + 1) / width, 4), round(float(bottom + 1) / tall, 4)],
                  darkest_shadow=round(float(ratio.min()), 3), grain=round(grain, 4), device=device, seconds=round(time.time() - started, 1))
    return report


def main():
    req = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
    out = Path(req['output_dir']); out.mkdir(parents=True, exist_ok=True)
    try:
        result = insert(req, out)
    except Unfit as exc:
        result = {'success': False, 'reason': 'no_fit', 'detail': str(exc)}
    except Exception as exc:
        traceback.print_exc(); worker.atomic(out / 'result.json', {'success': False, 'reason': 'error', 'error': str(exc)}); raise
    worker.atomic(out / 'result.json', result)


if __name__ == '__main__':
    main()

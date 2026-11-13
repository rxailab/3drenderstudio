"""Set dressing for usage-scene shots: props, an opened compartment, a real floor.

A layout is solved once, by measurement, and stored as plain transforms. Every
later render of that shot (thumbnail, preview, 2K, 4K) rebuilds exactly that
layout, so the picture does not change between qualities.

    solve()    rays onto the product decide where things can rest: its flat
               top, the inside of an opened drawer, the floor beside it
    apply()    rebuild a stored layout in a freshly prepared scene
    surface()  give the studio floor a real material and add a back wall

Props are never part of the product: they are imported after the product
objects were registered, carry the PREFIX below, and are therefore ignored by
framing, part inspection, product mattes and every other product-only step.
"""
import math
import random
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

PREFIX = 'Studio prop '
ZONES = ('on_top', 'beside', 'inside', 'behind')
POSES = ('auto', 'flat', 'upright')
GRID = 72
EXTENSION = .72          # how far a drawer is pulled out, as a share of its depth
FOCUS_FSTOP = 2.8        # product sharp front to back at this set's scale, the wall behind it soft
MAX_SCENERY = .14        # the most of the picture any one piece of scenery may take up


def _update():
    bpy.context.view_layer.update()


def world_box(objects):
    points = [ob.matrix_world @ Vector(corner) for ob in objects if ob.type == 'MESH' for corner in ob.bound_box]
    if not points:
        raise ValueError('没有可测量的网格')
    return (Vector([min(p[i] for p in points) for i in range(3)]), Vector([max(p[i] for p in points) for i in range(3)]))


def import_prop(path, ident):
    """Import one glTF prop under its own root. Returns (root, meshes)."""
    path = Path(path)
    if path.suffix.lower() not in ('.gltf', '.glb') or not path.is_file():
        raise ValueError('道具模型文件不存在：' + path.name)
    scene = bpy.context.scene
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    created = [ob for ob in bpy.data.objects if ob not in before]
    root = bpy.data.objects.new(PREFIX + ident, None); scene.collection.objects.link(root)
    for index, ob in enumerate(created):
        if ob.parent is None:
            matrix = ob.matrix_world.copy(); ob.parent = root; ob.matrix_world = matrix
        ob.name = '%s%s part %d' % (PREFIX, ident, index + 1)
        if ob.type in ('LIGHT', 'CAMERA'):
            ob.hide_render = True
        ob.select_set(False)
    meshes = [ob for ob in created if ob.type == 'MESH' and len(ob.data.polygons)]
    if not meshes:
        raise ValueError('道具模型没有可渲染的网格：' + path.name)
    _update()
    return root, meshes


def rest_rotation(size, pose):
    """How a prop lies when put down: its thinnest side up, unless it stands."""
    x, y, z = size
    flat = pose == 'flat' or (pose == 'auto' and z >= max(x, y) and z / max(min(x, y), 1e-9) > 4)
    if not flat:
        return Matrix.Identity(4)
    thinnest = min(range(3), key=lambda axis: size[axis])
    if thinnest == 1:
        return Matrix.Rotation(math.pi / 2, 4, 'X')
    if thinnest == 0:
        return Matrix.Rotation(math.pi / 2, 4, 'Y')
    return Matrix.Identity(4)


def place(root, meshes, base, position, yaw, height):
    """Put a prop's footprint centre at ``position`` with its underside at ``height``."""
    root.matrix_world = Matrix.Rotation(yaw, 4, 'Z') @ base
    _update()
    lo, hi = world_box(meshes)
    shift = Vector((position[0] - (lo.x + hi.x) / 2, position[1] - (lo.y + hi.y) / 2, height - lo.z))
    root.matrix_world = Matrix.Translation(shift) @ root.matrix_world
    _update()


def footprint(meshes, pad=0.0):
    """The convex outline of a prop seen from above, as an oriented rectangle."""
    points = [(ob.matrix_world @ Vector(corner)).to_2d() for ob in meshes for corner in ob.bound_box]
    best = None
    for step in range(0, 90, 6):
        angle = math.radians(step); axis = Vector((math.cos(angle), math.sin(angle))); normal = Vector((-axis.y, axis.x))
        a = [p.dot(axis) for p in points]; b = [p.dot(normal) for p in points]
        area = (max(a) - min(a)) * (max(b) - min(b))
        if best is None or area < best[0]:
            best = (area, axis, normal, min(a) - pad, max(a) + pad, min(b) - pad, max(b) + pad)
    _, axis, normal, a0, a1, b0, b1 = best
    return [axis * a + normal * b for a, b in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))]


def _separated(first, second):
    for polygon in (first, second):
        for index in range(len(polygon)):
            edge = polygon[(index + 1) % len(polygon)] - polygon[index]
            axis = Vector((-edge.y, edge.x))
            if axis.length < 1e-12:
                continue
            a = [p.dot(axis) for p in first]; b = [p.dot(axis) for p in second]
            if max(a) < min(b) or max(b) < min(a):
                return True
    return False


def overlaps(candidate, placed):
    return any(not _separated(candidate, other) for other in placed)


def _samples(polygon):
    centre = sum(polygon, Vector((0, 0))) / len(polygon)
    points = list(polygon) + [centre]
    for index in range(len(polygon)):
        points.append((polygon[index] + polygon[(index + 1) % len(polygon)]) / 2)
        points.append((polygon[index] + centre) / 2)
    return points


class HeightField:
    """What a ray straight down first meets, over a rectangle of the scene."""
    def __init__(self, lo, hi, top, allowed=None):
        self.lo = Vector((lo[0], lo[1])); self.size = Vector((hi[0] - lo[0], hi[1] - lo[1]))
        longest = max(self.size.x, self.size.y, 1e-9)
        self.nx = max(4, round(GRID * self.size.x / longest)); self.ny = max(4, round(GRID * self.size.y / longest))
        graph = bpy.context.evaluated_depsgraph_get(); scene = bpy.context.scene
        names = None if allowed is None else {ob.name for ob in allowed}
        self.cells = {}
        for ix in range(self.nx):
            for iy in range(self.ny):
                origin = Vector((self.lo.x + (ix + .5) * self.size.x / self.nx, self.lo.y + (iy + .5) * self.size.y / self.ny, top))
                hit, location, normal, _, ob, _ = scene.ray_cast(graph, origin, Vector((0, 0, -1)))
                # A level face whichever way it is wound: CAD exports often carry flipped normals.
                if hit and abs(normal.z) > .94 and (names is None or ob.original.name in names or ob.name in names):
                    self.cells[(ix, iy)] = location.z

    def level(self, lowest=False, minimum=.2, tolerance=.01):
        """The flat level that carries the most area: the highest one, or the lowest."""
        if not self.cells:
            return None
        heights = sorted(set(round(z / tolerance) for z in self.cells.values()), reverse=not lowest)
        need = minimum * self.nx * self.ny
        for key in heights:
            count = sum(1 for z in self.cells.values() if abs(z / tolerance - key) <= 1)
            if count >= need:
                values = [z for z in self.cells.values() if abs(z / tolerance - key) <= 1]
                return sum(values) / len(values)
        return None

    def supports(self, polygon, level, tolerance=.012):
        for point in _samples(polygon):
            ix = math.floor((point.x - self.lo.x) / self.size.x * self.nx); iy = math.floor((point.y - self.lo.y) / self.size.y * self.ny)
            z = self.cells.get((ix, iy))
            if z is None or abs(z - level) > tolerance:
                return False
        return True

    def area(self, level, tolerance=.012):
        cell = self.size.x / self.nx * self.size.y / self.ny
        return cell * sum(1 for z in self.cells.values() if abs(z - level) <= tolerance)


def _rect(scene, meshes):
    """The rectangle a set of meshes covers in the camera frame, (x0, y0, x1, y1) with the frame as 0..1."""
    from blender.camera_projection import frame_bounds
    points = [tuple(ob.matrix_world @ Vector(corner)) for ob in meshes for corner in ob.bound_box]
    rect, _ = frame_bounds(scene, points)
    return tuple(rect)


def _share(rect, other):
    """How much of ``rect`` lies inside ``other``, 0..1."""
    width = min(rect[2], other[2]) - max(rect[0], other[0]); height = min(rect[3], other[3]) - max(rect[1], other[1])
    area = (rect[2] - rect[0]) * (rect[3] - rect[1])
    return max(0, width) * max(0, height) / area if area > 1e-12 else 0


def find_compartment(products, keywords, toward):
    """The top drawer-like group of parts and the horizontal way it slides out.

    ``toward`` is the horizontal direction from the product to the camera. Parts
    are grouped by height; the group must reach the product's outer face on the
    side it opens to. Returns None when the names or the geometry do not say so
    clearly: nothing is opened on a guess.
    """
    words = [word.casefold() for word in keywords if isinstance(word, str) and word]
    named = [ob for ob in products if any(word in ob.name.casefold() for word in words)]
    if not named:
        return None
    lo, hi = world_box(products); span = max(hi - lo)
    boxes = sorted(((world_box([ob]), ob) for ob in named), key=lambda item: -item[0][1].z)
    groups = []
    for (low, high), ob in boxes:
        for group in groups:
            if low.z < group['hi'] - (group['hi'] - group['lo']) * .4 and high.z > group['lo'] + (group['hi'] - group['lo']) * .4:
                group['parts'].append(ob); group['lo'] = min(group['lo'], low.z); group['hi'] = max(group['hi'], high.z); break
        else:
            groups.append({'parts': [ob], 'lo': low.z, 'hi': high.z})
    for group in sorted(groups, key=lambda item: -item['hi']):
        low, high = world_box(group['parts'])
        if (high.z - low.z) < span * .02:
            continue
        best = None
        for axis, sign in ((0, 1), (0, -1), (1, 1), (1, -1)):
            face = high[axis] if sign > 0 else low[axis]; outer = hi[axis] if sign > 0 else lo[axis]
            direction = Vector((sign if axis == 0 else 0, sign if axis == 1 else 0, 0))
            facing = direction.to_2d().dot(toward)
            if abs(face - outer) <= span * .04 and facing > .2 and (best is None or facing > best[0]):
                best = (facing, direction, axis)
        if best is None:
            continue
        _, direction, axis = best
        depth = high[axis] - low[axis]
        grow = Vector((span * .01,) * 3); grow[axis] = span * .06
        members = []
        for ob in products:
            a, b = world_box([ob])
            if all(a[i] >= low[i] - grow[i] and b[i] <= high[i] + grow[i] for i in range(3)):
                members.append(ob)
        if depth < span * .08 or len(members) == len(products):
            continue
        return {'parts': members, 'direction': direction, 'depth': depth, 'lo': low, 'hi': high}
    return None


def move(objects, offset):
    for ob in objects:
        ob.matrix_world = Matrix.Translation(offset) @ ob.matrix_world
    _update()


def _yaw_choices(rng, along, spread):
    return [along + math.radians(rng.uniform(-spread, spread)) + (math.pi if rng.random() < .5 else 0) for _ in range(6)]


def solve(products, props, shot, units_per_meter, seed=1, open_keywords=(), frame=None, room=None):
    """Place imported props around a prepared product.

    ``props`` is a list of dicts with root, meshes, zone and pose; each gains
    'placed' (bool), 'zone_used' and 'reason'. ``frame(meshes)`` aims the scene
    camera at the product together with those meshes; it is called once, after
    everything that rests on or in the product is in place. ``room`` is the free
    depth behind the product, in product spans, up to the wall. Returns
    (opening, report, view) where view is whatever ``frame`` returned.
    """
    rng = random.Random(seed)
    lo, hi = world_box(products); span = max(hi - lo); centre = (lo + hi) / 2
    azimuth = math.radians(shot.get('azimuth', 35))
    toward = Vector((math.sin(azimuth), -math.cos(azimuth))); right = Vector((math.cos(azimuth), math.sin(azimuth)))
    for prop in props:
        prop['root'].matrix_world = Matrix.Scale(units_per_meter, 4); _update()
        low, high = world_box(prop['meshes'])
        prop['size'] = tuple(high - low)
        prop['base'] = rest_rotation(prop['size'], prop.get('pose', 'auto')) @ Matrix.Scale(units_per_meter, 4)
        prop['root'].matrix_world = prop['base']; _update()
        low, high = world_box(prop['meshes'])
        prop['rest'] = tuple(high - low); prop['placed'] = False; prop['zone_used'] = None; prop['reason'] = None
        prop['root'].matrix_world = Matrix.Translation((0, 0, -1000)) @ prop['base']        # out of every ray's way
    _update()

    # --- an opened compartment, only when something is meant to go inside it
    opening = None; cavity = None
    if any(prop['zone'] == 'inside' for prop in props) and open_keywords:
        found = find_compartment(products, open_keywords, toward)
        if found:
            offset = found['direction'] * found['depth'] * EXTENSION
            move(found['parts'], offset)
            a = found['lo'] + offset; b = found['hi'] + offset
            axis = 0 if abs(found['direction'].x) > .5 else 1
            outer = hi[axis] if found['direction'][axis] > 0 else lo[axis]
            a, b = Vector(a), Vector(b)
            if found['direction'][axis] > 0: a[axis] = outer
            else: b[axis] = outer
            field = HeightField(a, b, b.z + span * .02, allowed=found['parts'])
            # A drawer is hollow: its floor lies below the top of its own walls. A solid block is not opened.
            floor = field.level(lowest=True, minimum=.25); rim = found['hi'].z
            if floor is not None and rim - floor > span * .012:
                cavity = (field, floor, 0 if field.size.x >= field.size.y else math.pi / 2, -found['direction'].to_2d())
                opening = {'objects': [ob.name for ob in found['parts']], 'translation': [float(v) for v in offset],
                           'cavity_area': field.area(floor), 'cavity_depth': float(rim - floor)}
            else:
                move(found['parts'], -offset)
    blocked = []
    if opening:
        low, high = world_box([bpy.data.objects[name] for name in opening['objects']])
        blocked.append([Vector((low.x, low.y)), Vector((high.x, low.y)), Vector((high.x, high.y)), Vector((low.x, high.y))])

    top = HeightField(lo, hi, hi.z + span, allowed=products)
    plateau = top.level(minimum=.18)
    placed = {'on_top': [], 'inside': [], 'floor': list(blocked)}
    pad = span * .012

    def try_surface(prop, field, level, others, along, spread, bias, pad=pad):
        candidates = []
        for _ in range(420):
            ix, iy = rng.choice(list(field.cells))
            if abs(field.cells[(ix, iy)] - level) > .012:
                continue
            position = (field.lo.x + (ix + rng.random()) * field.size.x / field.nx, field.lo.y + (iy + rng.random()) * field.size.y / field.ny)
            own = 0 if prop['rest'][0] >= prop['rest'][1] else math.pi / 2
            for yaw in _yaw_choices(rng, along - own, spread)[:2]:
                place(prop['root'], prop['meshes'], prop['base'], position, yaw, level + span * .0006)
                outline = footprint(prop['meshes'], pad)
                if field.supports(outline, level) and not overlaps(outline, others):
                    candidates.append((bias(Vector(position)), position, yaw, outline))
            if len(candidates) >= 24:
                break
        if not candidates:
            return False
        _, position, yaw, _ = max(candidates, key=lambda item: item[0])
        place(prop['root'], prop['meshes'], prop['base'], position, yaw, level + span * .0006)
        others.append(footprint(prop['meshes'], pad))
        return True

    long_axis = 0 if (hi.x - lo.x) >= (hi.y - lo.y) else math.pi / 2
    order = sorted(props, key=lambda prop: -max(prop['rest'][0], prop['rest'][1]))
    floor_props = []
    for prop in order:
        zone = prop['zone']
        if zone == 'inside':
            if cavity and try_surface(prop, cavity[0], cavity[1], placed['inside'], cavity[2], 5,
                                      lambda p: p.dot(cavity[3]) + rng.random() * span * .01, pad * .5):
                prop.update(placed=True, zone_used='inside'); continue
            zone = 'on_top'; prop['reason'] = 'no_open_compartment'
        if zone == 'on_top':
            # Towards the camera-facing edge, where a hand would put a tool down.
            if plateau is not None and prop['rest'][2] < (hi.z - lo.z) * .45 and try_surface(
                    prop, top, plateau, placed['on_top'], long_axis, 38,
                    lambda p: (p - centre.to_2d()).dot(toward) * .6 + rng.random() * span * .25):
                prop.update(placed=True, zone_used='on_top'); continue
            zone = 'beside'; prop['reason'] = prop['reason'] or 'no_flat_top'
        floor_props.append((prop, zone))

    # --- the camera is set on the product and what rests on it, before anything goes on the floor.
    # Floor props are scenery: they are placed for that frame, out at its edges, and may be cut by it.
    view = frame([part for prop in props if prop['placed'] for part in prop['meshes']]) if frame else None
    scene = bpy.context.scene
    subject = _rect(scene, products) if view else None
    body = [Vector((lo.x, lo.y)), Vector((hi.x, lo.y)), Vector((hi.x, hi.y)), Vector((lo.x, hi.y))]
    half = Vector(((hi.x - lo.x) / 2, (hi.y - lo.y) / 2))
    reach_u = abs(right.x) * half.x + abs(right.y) * half.y; reach_v = abs(toward.x) * half.x + abs(toward.y) * half.y
    depth = max(.25, min(1.6, room)) if room else .25      # how far back scenery may stand before it meets the wall
    for prop, zone in floor_props:
        tall = prop['rest'][2] > (hi.z - lo.z) * .5 or zone == 'behind'
        radius = max(prop['rest'][0], prop['rest'][1]) / 2; narrow = min(prop['rest'][0], prop['rest'][1]) / 2
        best = None
        # Two passes: first as discreet scenery; if the product leaves no room for that, more boldly cropped.
        for attempt in range(1040):
            if attempt == 520 and best is not None:
                break
            limit_seen, limit_area = (.3, MAX_SCENERY) if attempt < 520 else (.12, MAX_SCENERY * 2)
            side = rng.choice((-1, 1)); reach = narrow + span * rng.uniform(.02, .3 if attempt < 520 else .7) + rng.random() * (radius - narrow)
            if tall:        # set back, to one side
                u = side * (reach_u * rng.uniform(.55, 1) + reach); v = rng.uniform(-reach_v - radius - span * depth, -reach_v * .1)
            else:           # at the side, never in front of the product
                u = side * (reach_u + reach); v = rng.uniform(-reach_v * .9, reach_v * .55)
            position = centre.to_2d() + right * u + toward * v
            yaw = rng.uniform(0, math.tau)
            place(prop['root'], prop['meshes'], prop['base'], position, yaw, span * .0006)
            outline = footprint(prop['meshes'], pad)
            if overlaps(outline, placed['floor'] + [body]):
                continue
            gap = min(max(lo.x - point.x, point.x - hi.x, lo.y - point.y, point.y - hi.y, 0) for point in outline)
            score = -gap * 1.5 + rng.random() * span * .03
            if subject:
                rect = _rect(scene, prop['meshes'])
                seen = _share(rect, (0, 0, 1, 1)); hidden = _share(rect, subject)
                covers = _share(subject, rect) if (position - centre.to_2d()).dot(toward) > 0 else 0
                visible = seen * (rect[2] - rect[0]) * (rect[3] - rect[1])        # share of the picture it takes up
                # Scenery must show, must not hide behind the product, and must neither cover nor outweigh it.
                if seen < limit_seen or hidden > .6 or covers > .04 or visible > limit_area or _share(subject, rect) > .18:
                    continue
                middle = ((rect[0] + rect[2]) / 2 - .5) ** 2 + ((rect[1] + rect[3]) / 2 - .5) ** 2
                score += middle * span * 1.2 - hidden * span * .4 + min(seen, .85) * span * .25
            if best is None or score > best[0]:
                best = (score, position, yaw)
        if best is None:
            prop['root'].matrix_world = Matrix.Translation((0, 0, -1000)) @ prop['base']
            prop['reason'] = 'no_room'; continue
        place(prop['root'], prop['meshes'], prop['base'], best[1], best[2], span * .0006)
        placed['floor'].append(footprint(prop['meshes'], pad))
        prop.update(placed=True, zone_used='behind' if tall else 'beside')
    _update()
    report = {'plateau_height': plateau, 'plateau_area': top.area(plateau) if plateau is not None else 0,
              'product_span': float(span), 'opened': bool(opening)}
    return opening, report, view


def apply(scene_set, products):
    """Rebuild a stored layout. Returns the prop meshes that were added."""
    names = {ob.name: ob for ob in products}
    opening = scene_set.get('open')
    if opening:
        missing = [name for name in opening['objects'] if name not in names]
        if missing:
            raise ValueError('场景布置引用的部件不存在：' + missing[0])
        move([names[name] for name in opening['objects']], Vector(opening['translation']))
    added = []
    for prop in scene_set.get('props', []):
        root, meshes = import_prop(prop['file'], prop['id'])
        root.matrix_world = Matrix([prop['matrix'][index * 4:index * 4 + 4] for index in range(4)])
        added.extend(meshes)
    _update()
    return added


def wall_gap(span, distance_m, units):
    """Free floor between the back of the product and the wall, in work units."""
    return max(span * .55, distance_m * units)


def _image(path, colorspace):
    image = bpy.data.images.load(str(path), check_existing=True)
    image.colorspace_settings.name = colorspace
    return image


def textured(name, maps, tile, color):
    """A PBR material from downloaded maps, tiled at real size on world coordinates."""
    material = bpy.data.materials.new(name); material.use_nodes = True
    nodes = material.node_tree.nodes; links = material.node_tree.links
    shader = next(node for node in nodes if node.type == 'BSDF_PRINCIPLED')
    shader.inputs['Base Color'].default_value = tuple(color) + (1,); shader.inputs['Roughness'].default_value = .7
    usable = {key: Path(value) for key, value in (maps or {}).items() if Path(value).is_file()}
    if not usable:
        return material
    coordinates = nodes.new('ShaderNodeTexCoord'); mapping = nodes.new('ShaderNodeMapping')
    mapping.inputs['Scale'].default_value = (1 / max(tile, 1e-6),) * 3
    links.new(coordinates.outputs['Object'], mapping.inputs['Vector'])
    def texture(key, colorspace):
        node = nodes.new('ShaderNodeTexImage'); node.image = _image(usable[key], colorspace)
        node.projection = 'BOX'; node.projection_blend = .15
        links.new(mapping.outputs['Vector'], node.inputs['Vector'])
        return node
    if 'base_color' in usable:
        links.new(texture('base_color', 'sRGB').outputs['Color'], shader.inputs['Base Color'])
    if 'roughness' in usable:
        links.new(texture('roughness', 'Non-Color').outputs['Color'], shader.inputs['Roughness'])
    if 'normal' in usable:
        normal = nodes.new('ShaderNodeNormalMap'); normal.inputs['Strength'].default_value = .8
        links.new(texture('normal', 'Non-Color').outputs['Color'], normal.inputs['Color'])
        links.new(normal.outputs['Normal'], shader.inputs['Normal'])
    return material


def surface(scene_set, shot, products):
    """Replace the studio sweep with the setting's own floor and a wall behind."""
    scene = bpy.context.scene
    floor = bpy.data.objects.get('Studio floor')
    ground = scene_set.get('surface') or {}
    units = scene_set['units_per_meter']
    record = {}
    if floor is not None and ground:
        material = textured('Studio scene floor', ground.get('textures'), ground.get('tile_m', 2) * units,
                            ground.get('color', (.5, .5, .5)))
        floor.data.materials.clear(); floor.data.materials.append(material)
        floor.is_shadow_catcher = False
        record['floor'] = ground.get('asset_id') or 'color'
    backdrop = scene_set.get('wall')
    if backdrop:
        lo, hi = world_box(products); span = max(hi - lo)
        azimuth = math.radians(shot.get('azimuth', 35))
        away = Vector((-math.sin(azimuth), math.cos(azimuth), 0))
        # A wall the product stands in front of, square to the room rather than to the lens.
        cardinal = max((Vector((1, 0, 0)), Vector((-1, 0, 0)), Vector((0, 1, 0)), Vector((0, -1, 0))), key=lambda v: v.dot(away))
        reach = abs(cardinal.x) * (hi.x - lo.x) / 2 + abs(cardinal.y) * (hi.y - lo.y) / 2
        distance = reach + wall_gap(span, backdrop.get('distance_m', .6), units)
        # Built at its real size so the texture tiles in world units like the floor's.
        across = Vector((-cardinal.y, cardinal.x, 0)); half = span * 20; height = span * 8
        corners = [cardinal * distance + across * u + Vector((0, 0, z)) for u, z in ((-half, 0), (half, 0), (half, height), (-half, height))]
        mesh = bpy.data.meshes.new('Studio scene wall'); mesh.from_pydata([tuple(corner) for corner in corners], [], [(0, 1, 2, 3)])
        mesh.update()
        wall = bpy.data.objects.new('Studio scene wall', mesh); scene.collection.objects.link(wall)
        material = textured('Studio scene wall', backdrop.get('textures'), backdrop.get('tile_m', 2) * units,
                            backdrop.get('color', (.62, .62, .6)))
        wall.data.materials.append(material)
        record['wall'] = backdrop.get('asset_id') or 'color'
    # The product's shaping lamps skip the set. One broad, soft source from the
    # camera side lights the floor and wall evenly, so the room reads as a room.
    targets = [ob for ob in (floor, bpy.data.objects.get('Studio scene wall')) if ob is not None]
    if targets and 'light_linking' in bpy.types.Object.bl_rna.properties:
        lo, hi = world_box(products); span = max(hi - lo)
        azimuth = math.radians(shot.get('azimuth', 35))
        data = bpy.data.lights.new('Room light', 'AREA'); data.shape = 'RECTANGLE'; data.size = span * 5; data.size_y = span * 3
        data.energy = 200 if scene_set.get('setting') == 'workshop' else 330; data.color = (1, .98, .95)
        lamp = bpy.data.objects.new('Room light', data); scene.collection.objects.link(lamp)
        lamp.location = Vector((math.sin(azimuth) * span * 1.6, -math.cos(azimuth) * span * 1.6, span * 3.2))
        lamp.rotation_euler = (Vector((0, 0, span * .4)) - lamp.location).to_track_quat('-Z', 'Y').to_euler()
        lamp.visible_camera = False; data.use_shadow = False
        room = bpy.data.collections.new('Studio room receivers')
        for ob in targets: room.objects.link(ob)
        lamp.light_linking.receiver_collection = room
        record['room_light'] = data.energy
    _focus(shot, products)
    _update()
    return record


def _focus(shot, products):
    """Shallow focus held on the product, so the room and what stands in it fall softly behind."""
    if shot.get('projection') != 'perspective':
        return
    lo, hi = world_box(products); centre = (lo + hi) / 2

    def set_focus(scene, *_):
        camera = scene.camera
        if camera is None or camera.type != 'CAMERA' or camera.data.type != 'PERSP':
            return
        camera.data.dof.use_dof = True
        camera.data.dof.focus_distance = max((camera.matrix_world.translation - centre).length, .01)
        camera.data.dof.aperture_fstop = FOCUS_FSTOP
    # Appended now, so it runs after the camera adapter's own render_pre has applied the view.
    bpy.app.handlers.render_pre.append(set_focus)
    set_focus(bpy.context.scene)

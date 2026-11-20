"""Moving parts of a product model: find them, solve their joints, pose them.

Pliers open about their rivet, a caliper's slider runs along its beam, a tape's hook pulls the blade out of the case,
a toolbox lid swings up. A model file rarely says so (no armature, no animation), so the application asks a vision
model which numbered parts move and how (articulation.py), and this script does the geometry:

* ``probe``  splits the model into parts (objects, or the loose pieces of a one-piece mesh such as a caliper), and
             renders them colour-coded from above and at three-quarters, with each part's label point, so the
             vision model can name them by number;
* ``solve``  turns the vision model's joints (which parts move, about what, which way is "open") into numbers: a
             pivot and an axis, the sign that opens rather than drives one part through another (found by
             collision tests), a safe range, the beam a slider leaves bare, and a preview of every state;
* ``apply``  poses a prepared scene (worker.prepare calls it): every render path then shows the chosen state.

Joint values are degrees for a hinge (each moving group turns by the value, a counter group by minus the value)
and millimetres for a slide. All geometry is in the model's import frame, so it does not depend on the project's
display rotation; ``apply`` maps it through the studio's root transform.
"""
import json
import math
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from bpy_extras.object_utils import world_to_camera_view

MAX_SPLIT_VERTS = 300000          # a mesh larger than this is one part (a 500k-vertex grinder gear is not split)
MAX_PIECES = 40                   # more loose pieces than this (a knurled grip) and the object stays one part
MAX_SPLIT_OBJECTS = 6             # loose pieces are only looked at in models made of a few objects
PALETTE = [(0.90, 0.10, 0.10), (0.10, 0.45, 0.90), (0.10, 0.70, 0.25), (0.95, 0.60, 0.05), (0.60, 0.20, 0.80),
           (0.05, 0.75, 0.80), (0.85, 0.30, 0.55), (0.55, 0.40, 0.15), (0.40, 0.75, 0.05), (0.15, 0.20, 0.55),
           (0.95, 0.85, 0.10), (0.50, 0.50, 0.50)]


# ---------- parts ----------

def _mesh_objects():
    return [ob for ob in bpy.context.scene.objects if ob.type == 'MESH' and len(ob.data.vertices)]


_PIECES = {}


def pieces(ob):
    """The loose pieces of a mesh object as an array (vertex -> piece index), seams welded, ordered by their lowest
    vertex index so the numbering is the same every time the file is read."""
    key = (ob.name, ob.data.name, len(ob.data.vertices))
    if key in _PIECES:
        return _PIECES[key]
    me = ob.data; count = len(me.vertices)
    co = np.empty(count * 3); me.vertices.foreach_get('co', co); co = co.reshape(-1, 3)
    size = float(np.ptp(co, axis=0).max()) or 1.0
    eps = size * 1e-5
    _, weld = np.unique(np.round(co / eps).astype(np.int64), axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    parent = np.arange(weld.max() + 1)

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root
    edges = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get('vertices', edges)
    for a, b in weld[edges].reshape(-1, 2):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([find(i) for i in range(len(parent))])[weld]
    first = {}
    for index, root in enumerate(roots):
        first.setdefault(int(root), index)
    order = {root: rank for rank, root in enumerate(sorted(first, key=first.get))}
    labels = np.array([order[int(root)] for root in roots])
    _PIECES[key] = labels
    return labels


def _world(ob):
    me = ob.data; co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3); m = np.array(ob.matrix_world)
    return co @ m[:3, :3].T + m[:3, 3]


def part_list(unit_mm):
    """[{id, object, pieces|None, verts, bbox_mm, centroid_mm, size_mm, materials, label_point}] in the import frame. The
    loose pieces of a one-piece mesh are parts of their own; tiny ones of one material (printed legends, screws) are
    one part together, so the vision model is not asked about forty specks."""
    objects = _mesh_objects()
    split = len(objects) <= MAX_SPLIT_OBJECTS
    longest = float(np.ptp(np.concatenate([_world(ob) for ob in objects]), axis=0).max()) or 1
    out = []
    for ob in sorted(objects, key=lambda o: o.name):
        world = _world(ob)
        labels = None
        if split and len(ob.data.vertices) <= MAX_SPLIT_VERTS:
            labels = pieces(ob)
            if not (1 < labels.max() + 1 <= MAX_PIECES):
                labels = None
        mats = _face_materials(ob, labels)
        if labels is None:
            groups = [(None, np.arange(len(world)))]
        else:
            groups, small = [], {}
            for k in range(labels.max() + 1):
                index = np.where(labels == k)[0]
                if np.ptp(world[index], axis=0).max() < longest * .04:
                    small.setdefault((mats.get(k) or [''])[0], []).append(k)
                else:
                    groups.append(([k], index))
            for material, ks in small.items():
                groups.append((ks, np.where(np.isin(labels, ks))[0]))
            mats = {tuple(ks): mats.get(ks[0], []) for ks, _ in groups}
        for ks, index in groups:
            pts = world[index]
            centre = pts.mean(0)
            near = pts[np.argmin(((pts - centre) ** 2).sum(1))]
            out.append({'id': len(out) + 1, 'object': ob.name, 'pieces': ks, 'verts': int(len(index)),
                        'bbox_mm': [(pts.min(0) * unit_mm).round(1).tolist(), (pts.max(0) * unit_mm).round(1).tolist()],
                        'size_mm': (np.ptp(pts, axis=0) * unit_mm).round(1).tolist(), 'centroid_mm': (centre * unit_mm).round(1).tolist(),
                        'materials': mats.get(None if ks is None else tuple(ks), []), 'label_point': near.tolist(),
                        'parent': ob.parent.name if ob.parent else None})
    return out


def _face_materials(ob, labels):
    me = ob.data; found = {}
    names = [m.name if m else '' for m in me.materials]
    for poly in me.polygons:
        piece = None if labels is None else int(labels[poly.vertices[0]])
        name = names[poly.material_index] if poly.material_index < len(names) else ''
        bucket = found.setdefault(piece, {}); bucket[name] = bucket.get(name, 0) + 1
    return {piece: [name for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:2] if name] for piece, counts in found.items()}


def selector_indices(ob, selector):
    """The vertex indices of ``ob`` a selector takes: all of them, or its listed loose pieces."""
    if not selector.get('pieces'):
        return np.arange(len(ob.data.vertices))
    return np.where(np.isin(pieces(ob), selector['pieces']))[0]


# ---------- posing ----------

def _rotation(pivot, axis, degrees):
    p = Vector(pivot)
    return Matrix.Translation(p) @ Matrix.Rotation(math.radians(degrees), 4, Vector(axis).normalized()) @ Matrix.Translation(-p)


def _members(joint, role):
    return [row for row in joint.get(role) or [] if bpy.data.objects.get(row.get('object', ''))]


def _top_objects(rows):
    """Whole-object selections without one whose ancestor is also selected (the child follows its parent)."""
    names = {row['object'] for row in rows if not row.get('pieces')}
    out = []
    for name in names:
        ob = bpy.data.objects[name]; parent = ob.parent; skip = False
        while parent is not None:
            if parent.name in names:
                skip = True; break
            parent = parent.parent
        if not skip:
            out.append(ob)
    return out


def _detach_crossing_children(joints, root=None):
    """A child that belongs to another group than its parent (a tape blade under its hook) is unparented, keeping its
    place, so moving the parent does not drag it along."""
    role = {}
    for joint in joints:
        for name in ('moving', 'counter', 'stretch'):
            for row in _members(joint, name):
                if not row.get('pieces'):
                    role[row['object']] = (joint.get('name'), name)
    for ob in list(bpy.context.scene.objects):
        parent = ob.parent
        if parent is None:
            continue
        mine = role.get(ob.name); theirs = None; up = parent
        while up is not None and theirs is None:
            theirs = role.get(up.name); up = up.parent
        if mine is not None and mine != theirs:
            matrix = ob.matrix_world.copy(); ob.parent = root; ob.matrix_world = matrix
    bpy.context.view_layer.update()


def _transform_vertices(ob, index, world_matrix):
    """Move some vertices of ``ob`` by a world-space matrix (its mesh is made single-user first)."""
    if ob.data.users > 1:
        ob.data = ob.data.copy()
    me = ob.data; co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get('co', co); co = co.reshape(-1, 3)
    m = np.array(ob.matrix_world.inverted() @ world_matrix @ ob.matrix_world)
    co[index] = co[index] @ m[:3, :3].T + m[:3, 3]
    me.vertices.foreach_set('co', co.reshape(-1)); me.update()


def _move(rows, world_matrix):
    for ob in _top_objects(rows):
        ob.matrix_world = world_matrix @ ob.matrix_world
    for row in rows:
        if row.get('pieces'):
            ob = bpy.data.objects[row['object']]
            _transform_vertices(ob, selector_indices(ob, row), world_matrix)
    bpy.context.view_layer.update()


def _stretch(rows, frame, axis, start, end, new_end):
    """Vertices of a flexible part (a tape blade) beyond ``start`` along the axis are spread between start and the
    moving part's new position, so the blade follows the hook in or out."""
    if abs(end - start) < 1e-9:
        return
    factor = (new_end - start) / (end - start)
    a = np.array(Vector(axis).normalized())
    for row in rows:
        ob = bpy.data.objects[row['object']]
        if ob.data.users > 1:
            ob.data = ob.data.copy()
        me = ob.data; co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get('co', co); co = co.reshape(-1, 3)
        to_frame = np.array(frame.inverted() @ ob.matrix_world); back = np.array(ob.matrix_world.inverted() @ frame)
        pts = co @ to_frame[:3, :3].T + to_frame[:3, 3]
        index = selector_indices(ob, row)
        t = pts[index] @ a
        beyond = t > start
        shift = np.zeros_like(t); shift[beyond] = (start + (t[beyond] - start) * factor) - t[beyond]
        pts[index] += shift[:, None] * a
        co = pts @ back[:3, :3].T + back[:3, 3]
        me.vertices.foreach_set('co', co.reshape(-1)); me.update()


def _fill(fill, frame, value_units, axis):
    """Copies of the rail a slider runs on, laid where the slider no longer covers it (a caliper beam is often only
    modelled where it shows). Returns the new objects."""
    made = []
    if value_units <= 0:
        return made
    a = Vector(axis).normalized()
    for row in fill:
        ob = bpy.data.objects.get(row['source'].get('object', ''))
        if ob is None:
            continue
        bm = bmesh.new(); bm.from_mesh(ob.data)
        if row['source'].get('pieces'):
            keep = set(selector_indices(ob, row['source']).tolist()); bm.verts.ensure_lookup_table()
            bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.verts[0].index not in keep], context='FACES')
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
        local = ob.matrix_world.inverted() @ frame            # import frame -> object local
        shift = local.to_3x3() @ (a * row['shift'])
        bmesh.ops.translate(bm, verts=bm.verts, vec=shift)
        normal_local = (local.inverted().transposed().to_3x3() @ a).normalized()
        low = local @ (a * row['clip'][0]); high = local @ (a * min(row['clip'][1], row['clip'][0] + value_units + row.get('pad', 0)))
        for point, keep_side in ((low, 1), (high, -1)):
            geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
            bmesh.ops.bisect_plane(bm, geom=geom, plane_co=point, plane_no=normal_local * keep_side, clear_inner=True)
        if not bm.faces:
            bm.free(); continue
        me = bpy.data.meshes.new(ob.data.name + '·fill'); bm.to_mesh(me); bm.free()
        for material in ob.data.materials:
            me.materials.append(material)
        copy = bpy.data.objects.new(ob.name + '·fill', me)
        for collection in ob.users_collection:
            collection.objects.link(copy)
        copy.parent = ob.parent; copy.matrix_world = ob.matrix_world.copy()
        made.append(copy)
    bpy.context.view_layer.update()
    return made


def apply(articulation, root=None):
    """Pose the scene: each joint at its value. ``root`` is the studio's root transform the model was put under after
    import (None while probing). Returns the objects made (rail fills), which the caller renders with the rest."""
    joints = [joint for joint in (articulation or {}).get('joints') or [] if joint.get('value')]
    if not joints:
        return []
    frame = root.matrix_world.copy() if root is not None else Matrix.Identity(4)
    _detach_crossing_children(joints, root)
    made = []
    for joint in joints:
        value = float(joint['value']); axis = joint['axis']
        if joint['type'] == 'hinge':
            turn = frame @ _rotation(joint['pivot'], axis, joint.get('sign', 1) * value) @ frame.inverted()
            _move(_members(joint, 'moving'), turn)
            if joint.get('counter'):
                back = frame @ _rotation(joint['pivot'], axis, -joint.get('sign', 1) * value) @ frame.inverted()
                _move(_members(joint, 'counter'), back)
        else:
            units = value / float(joint.get('unit_mm') or 1000) * joint.get('sign', 1)
            vector = Vector(axis).normalized() * units
            _move(_members(joint, 'moving'), frame @ Matrix.Translation(vector) @ frame.inverted())
            if joint.get('stretch') and joint.get('stretch_span'):
                start, end = joint['stretch_span']
                along = Vector(axis).normalized() * joint.get('sign', 1)
                _stretch(_members(joint, 'stretch'), frame, list(along), start, end, end + value / float(joint.get('unit_mm') or 1000))
            made += _fill(joint.get('fill') or [], frame, value / float(joint.get('unit_mm') or 1000), list(Vector(axis).normalized() * joint.get('sign', 1)))
    return made


ENVELOPE = []          # corner points of every state asked for (worker.prepare -> stage framing)


def envelope(articulations, root=None):
    """The bounding corners of the model in each of these states (from the rest pose), kept in ENVELOPE."""
    ENVELOPE.clear()
    for articulation in articulations or []:
        snap = Snapshot()
        apply(articulation, root)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        for ob in _mesh_objects():
            ENVELOPE.extend(ob.evaluated_get(dg).matrix_world @ Vector(corner) for corner in ob.evaluated_get(dg).bound_box)
        snap.restore()
    return ENVELOPE


# ---------- solving ----------

def _group_points(rows):
    pts = []
    for row in rows:
        ob = bpy.data.objects[row['object']]
        world = _world(ob)
        if not row.get('pieces'):
            pts.append(world)
            for child in ob.children_recursive:
                if child.type == 'MESH' and len(child.data.vertices):
                    pts.append(_world(child))
        else:
            pts.append(world[selector_indices(ob, row)])
    return np.concatenate(pts) if pts else np.zeros((0, 3))


def _bvh(rows):
    """One BVH tree over the selected geometry, in world space."""
    verts, polys = [], []
    for row in rows:
        ob = bpy.data.objects[row['object']]
        obs = [ob] if row.get('pieces') else [ob] + [c for c in ob.children_recursive if c.type == 'MESH']
        for item in obs:
            world = _world(item); offset = len(verts)
            keep = set(selector_indices(item, row).tolist()) if item is ob and (row.get('pieces')) else None
            verts.extend(map(tuple, world))
            for poly in item.data.polygons:
                if keep is None or poly.vertices[0] in keep:
                    polys.append([offset + v for v in poly.vertices])
    return BVHTree.FromPolygons(verts, polys, epsilon=0.0) if polys else None


def _fixed_rows(joint, all_rows):
    """The parts a joint does not move: not selected, and not a child of a whole object that is."""
    taken = {}
    for role in ('moving', 'counter', 'stretch'):
        for row in joint.get(role) or []:
            taken.setdefault(row['object'], set()).update(row.get('pieces') or ['*'])
    whole = {name for name, pieces_ in taken.items() if '*' in pieces_}
    def carried(name):
        ob = bpy.data.objects.get(name)
        while ob is not None:
            if ob.name in whole:
                return True
            ob = ob.parent
        return False
    def selected(row):
        picked = taken.get(row['object'])
        return bool(picked) and ('*' in picked or set(row.get('pieces') or ['*']) <= picked)
    return [row for row in all_rows if not selected(row) and not carried(row['object'])]


def _collisions(joint, others):
    """How many triangle pairs of the moving group cross the rest (the counter group and the fixed parts)."""
    moving = _bvh(_members(joint, 'moving'))
    if moving is None:
        return 0
    total = 0
    for rows in others:
        tree = _bvh(rows)
        if tree is not None:
            total += len(moving.overlap(tree))
    return total


class Snapshot:
    """Every object's transform and every mesh's vertices, to undo a trial pose."""
    def __init__(self):
        self.matrix = {ob.name: ob.matrix_world.copy() for ob in bpy.context.scene.objects}
        self.parent = {ob.name: ob.parent for ob in bpy.context.scene.objects}
        self.co = {}
        for ob in _mesh_objects():
            co = np.empty(len(ob.data.vertices) * 3); ob.data.vertices.foreach_get('co', co); self.co[ob.data.name] = co
        self.objects = set(bpy.data.objects.keys())

    def restore(self):
        for name in set(bpy.data.objects.keys()) - self.objects:
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
        for ob in bpy.context.scene.objects:
            ob.parent = self.parent.get(ob.name)
        for ob in bpy.context.scene.objects:
            if ob.name in self.matrix:
                ob.matrix_world = self.matrix[ob.name]
        for ob in _mesh_objects():
            co = self.co.get(ob.data.name)
            if co is not None and len(co) == len(ob.data.vertices) * 3:
                ob.data.vertices.foreach_set('co', co); ob.data.update()
        bpy.context.view_layer.update()


def _axis_vector(name):
    return {'x': [1, 0, 0], 'y': [0, 1, 0], 'z': [0, 0, 1]}.get(str(name).lower().lstrip('+-'), [0, 0, 1])


def _pivot(joint, hint, all_rows):
    moving = _members(joint, 'moving'); counter = _members(joint, 'counter')
    kind = (hint or {}).get('kind', 'auto')
    origin = None
    if moving:
        ob = bpy.data.objects[moving[0]['object']]
        if not moving[0].get('pieces'):
            origin = np.array(ob.matrix_world.translation)
    pts = _group_points(moving)
    lo, hi = pts.min(0), pts.max(0)
    inside = origin is not None and np.all(origin >= lo - 1e-6) and np.all(origin <= hi + 1e-6) and np.linalg.norm(origin) > 1e-9
    if counter and inside:
        other = bpy.data.objects[counter[0]['object']]
        if not counter[0].get('pieces') and np.linalg.norm(np.array(other.matrix_world.translation) - origin) < 1e-3 * max(np.ptp(pts, axis=0).max(), 1e-6):
            return origin, 'shared origin'              # both halves are modelled about the rivet: nothing is surer than that
    if kind == 'part' and hint.get('part') is not None:
        pin = [row for row in all_rows if row['id'] == hint['part']]
        if pin:
            ob = bpy.data.objects[pin[0]['object']]           # the pin's own geometry, not the halves hung under it
            return _world(ob)[selector_indices(ob, pin[0])].mean(0), 'part'
    if kind in ('origin', 'auto') and inside:
        if not counter:
            return origin, 'origin'
        other = bpy.data.objects[counter[0]['object']].matrix_world.translation
        if np.linalg.norm(np.array(other) - origin) < 1e-3 * max(np.ptp(pts, axis=0).max(), 1e-6):
            return origin, 'origin'
    if counter and kind in ('crossing', 'auto', 'origin', 'part'):
        found = _crossing(joint)
        if found is not None:
            return found, 'crossing'
    if kind == 'edge' or not counter:
        side = str((hint or {}).get('side') or '-y')
        axis_index = 'xyz'.index(side[-1]) if side[-1] in 'xyz' else 1
        point = (lo + hi) / 2
        point[axis_index] = lo[axis_index] if side.startswith('-') else hi[axis_index]
        hinge_axis = int(np.argmax(np.abs(_axis_vector(joint.get('axis_name', 'x')))))
        rest = [i for i in range(3) if i not in (axis_index, hinge_axis)]
        fixed = _group_points(_fixed_rows(joint, all_rows))
        if rest and len(fixed):
            r = rest[0]; centre = fixed.mean(0)[r]
            point[r] = lo[r] if abs(lo[r] - centre) < abs(hi[r] - centre) else hi[r]
        return point, 'edge'
    return (lo + hi) / 2, 'centre'


def _spread(joint):
    """How far apart the two halves of a scissor pair reach: the distance between their far ends, both ways along
    the tool's length, added up."""
    a = _group_points(_members(joint, 'moving')); b = _group_points(_members(joint, 'counter'))
    if not len(a) or not len(b):
        return 0.0
    both = np.concatenate([a, b]); centre = both.mean(0)
    w, v = np.linalg.eigh(np.cov((both - centre).T)); length = v[:, w.argmax()]
    total = 0.0
    for direction in (1, -1):
        ta, tb = (a - centre) @ length * direction, (b - centre) @ length * direction
        ea = a[ta > np.quantile(ta, .97)].mean(0); eb = b[tb > np.quantile(tb, .97)].mean(0)
        total += float(np.linalg.norm(ea - eb))
    return total


def _crossing(joint):
    """Where two scissor halves cross: along the tool's length, the slice where the halves swap sides."""
    a = _group_points(_members(joint, 'moving')); b = _group_points(_members(joint, 'counter'))
    if not len(a) or not len(b):
        return None
    both = np.concatenate([a, b]); centre = both.mean(0)
    w, v = np.linalg.eigh(np.cov((both - centre).T)); order = w.argsort()[::-1]
    length, width = v[:, order[0]], v[:, order[1]]
    ta, tb = (a - centre) @ length, (b - centre) @ length
    low, high = max(ta.min(), tb.min()), min(ta.max(), tb.max())
    if high <= low:
        return None
    slices = np.linspace(low, high, 41); diffs = []
    step = (high - low) / 40
    for s in slices:
        ma = np.abs(ta - s) < step; mb = np.abs(tb - s) < step
        if ma.sum() < 3 or mb.sum() < 3:
            diffs.append(None); continue
        diffs.append(((a[ma] - centre) @ width).mean() - ((b[mb] - centre) @ width).mean())
    best = None
    for i in range(len(slices) - 1):
        d0, d1 = diffs[i], diffs[i + 1]
        if d0 is not None and d1 is not None and d0 * d1 < 0:
            s = slices[i] + (slices[i + 1] - slices[i]) * abs(d0) / (abs(d0) + abs(d1))
            if best is None or abs(s) < abs(best):
                best = s
    if best is None:
        return None
    near = np.concatenate([a[np.abs(ta - best) < step * 2], b[np.abs(tb - best) < step * 2]])
    return near.mean(0) if len(near) else centre + length * best


def solve_joint(joint, all_rows, unit_mm):
    """Fill in a joint the vision model described: pivot, axis vector, opening sign, safe maximum, rail fill, stretch span."""
    axis = _axis_vector(joint.get('axis_name', 'z'))
    joint['axis'] = axis; joint['unit_mm'] = unit_mm
    fixed = _fixed_rows(joint, all_rows)
    others = [rows for rows in (_members(joint, 'counter'), fixed) if rows]
    notes = []
    if joint['type'] == 'hinge':
        pivot, how = _pivot(joint, joint.get('pivot_hint'), all_rows)
        joint['pivot'] = [float(v) for v in pivot]; notes.append('pivot by %s' % how)
    else:
        joint['pivot'] = [0.0, 0.0, 0.0]
    maximum = float(joint.get('max') or (25 if joint['type'] == 'hinge' else 50))
    trial = maximum * .3
    base = _collisions(joint, others)
    scores = {}
    for sign in (1, -1):
        snap = Snapshot(); joint['sign'] = sign
        apply({'joints': [{**joint, 'value': trial, 'fill': [], 'stretch': []}]})
        scores[sign] = _collisions(joint, others) - base
        if joint.get('away_from') is not None:
            ref = [row for row in all_rows if row['id'] == joint['away_from']]
            if ref:
                scores[sign] = (scores[sign], -float(np.linalg.norm(_group_points(_members(joint, 'moving')).mean(0) - _group_points(ref).mean(0))))
        snap.restore()
    if joint['type'] == 'hinge' and joint.get('counter'):
        # a scissor pair: opening moves the far ends of the two halves apart; closing first brings them together
        for sign in (1, -1):
            snap = Snapshot(); joint['sign'] = sign
            apply({'joints': [{**joint, 'value': trial, 'fill': [], 'stretch': []}]})
            spread = _spread(joint)
            snap.restore()
            hits = scores[sign] if not isinstance(scores[sign], tuple) else scores[sign][0]
            scores[sign] = (hits, -spread)
        rest = _spread(joint)
        a, b = scores[1], scores[-1]
        # The spread decides: halves are often modelled interlocking at the rivet, and how many faces cross there rises
        # and falls with the angle. Contact only breaks a tie.
        if abs(a[1] - b[1]) > .1 * max(abs(a[1]), abs(b[1]), 1e-9):
            scores = {1: (a[1], a[0]), -1: (b[1], b[0])}
        notes.append('spread at rest %.4f, open %+.4f / %+.4f' % (rest, -a[1], -b[1]))
    def key(sign):
        value = scores[sign]
        return value if isinstance(value, tuple) else (value, 0)
    joint['sign'] = min((1, -1), key=key)
    if joint['type'] == 'hinge' and joint.get('counter'):
        # the halves of a scissor pair touch round the rivet in any position: contact does not bound their range
        joint['max'] = round(maximum, 2); joint['notes'] = notes
        return joint
    notes.append('sign %+d (crossings %s)' % (joint['sign'], scores))
    if joint['type'] == 'slide':
        # a slider runs inside its housing and touches it all the way: contact says nothing about its range. Its limits
        # are geometric: it keeps 40 % of its length in the parts that guide it, and goes in no further than flush.
        low, high = _slide_limits(joint, fixed, unit_mm)
        if joint.get('stretch'):
            # a tape could be pulled out for metres; a picture shows a hand's length of it next to the case
            whole = np.concatenate([_world(ob) for ob in _mesh_objects()])
            high = min(high, 1.5 * float(np.ptp(whole, axis=0).max()) * unit_mm)
        joint['max'] = round(min(maximum, high), 2); joint['min'] = round(low, 2); joint['notes'] = notes
        if high < maximum:
            notes.append('range limited to %.1f mm so it stays in its guide' % high)
        _slide_extras(joint, fixed)
        return joint
    # the largest value, in steps, that does not drive the moving parts into the rest
    safe = 0.0
    for k in range(1, 11):
        value = maximum * k / 10
        snap = Snapshot()
        apply({'joints': [{**joint, 'value': value, 'fill': [], 'stretch': []}]})
        hits = _collisions(joint, others) - base
        snap.restore()
        if hits > max(4, base * .05):
            break
        safe = value
    joint['max'] = round(safe or maximum * .1, 2)
    if safe < maximum:
        notes.append('range limited to %.1f by contact' % joint['max'])
    joint['notes'] = notes
    return joint


def _slide_limits(joint, fixed, unit_mm):
    """(lowest, highest) value in mm a slide can take: out no further than leaves 40 % of the moving part's length inside
    the fixed parts it runs in (those sharing its cross-section), in no further than where it sits flush with them (a blade
    or a tape that is out), never below 0 for one that is in already."""
    a = np.array(joint['axis'], dtype=float) * joint['sign']
    moving = _group_points(_members(joint, 'moving'))
    if not len(moving):
        return 0.0, float('inf')
    tm = moving @ a
    other = [i for i in range(3) if abs(a[i]) < .5]; mlo, mhi = moving.min(0), moving.max(0)
    guide = []
    for row in fixed:
        pts = _group_points([row])
        if all(pts[:, i].max() > mlo[i] and pts[:, i].min() < mhi[i] for i in other):
            guide.append(pts @ a)
    if not guide:
        return 0.0, float('inf')
    g_hi = max(float(t.max()) for t in guide)
    if joint.get('stretch'):
        high = float('inf')                       # a tape's hook is meant to leave its case
    elif _rides_on_rail(joint, fixed):
        # a slider round a beam (a caliper) stays on it all the way: its far end no further than the beam's
        high = max(0.0, g_hi - float(tm.max()) - .1 * float(np.ptp(tm))) * unit_mm      # short of the end cap
    else:
        # each moving part keeps 40 % of its own length in the guide (the blade, not the thumb button riding with it)
        high = min(max(0.0, g_hi - float((pts @ a).min()) - .4 * float(np.ptp(pts @ a)))
                   for pts in (_group_points([row]) for row in _members(joint, 'moving')) if len(pts)) * unit_mm
    if joint.get('stretch'):
        blade = _group_points(_members(joint, 'stretch'))
        out = float((blade @ a).max()) - g_hi if len(blade) else 0.0
    else:
        out = float(tm.max()) - g_hi
    low = -max(0.0, out) * unit_mm
    return low, high


def _rides_on_rail(joint, fixed):
    """Whether the moving group runs along a fixed part that starts where it ends and is longer than it (a caliper's
    slider on its beam), rather than inside a body it slides out of (a knife blade)."""
    a = np.array(joint['axis'], dtype=float) * joint['sign']
    moving = _group_points(_members(joint, 'moving'))
    if not len(moving):
        return False
    tm = moving @ a; length = float(tm.max() - tm.min())
    other = [i for i in range(3) if abs(a[i]) < .5]; mlo, mhi = moving.min(0), moving.max(0)
    for row in fixed:
        pts = _group_points([row]); t = pts @ a
        if all(pts[:, i].max() > mlo[i] and pts[:, i].min() < mhi[i] for i in other) \
                and float(t.max()) > float(tm.max()) + length and float(t.min()) <= float(tm.max()) + length * .05:
            return True
    return False


def _slide_extras(joint, fixed):
    """For a slide: the span a flexible part stretches over, and copies of a rail the slider leaves bare."""
    a = np.array(joint['axis'], dtype=float) * joint['sign']
    moving = _group_points(_members(joint, 'moving'))
    if not len(moving):
        return
    tm = moving @ a
    if joint.get('stretch'):
        body = _group_points(fixed); blade = _group_points(_members(joint, 'stretch'))
        if len(body) and len(blade):
            # from the case mouth to the blade's tip (under the hook): the stretch keeps the tip with the hook
            start = float((body @ a).max()); tip = float((blade @ a).max())
            joint['stretch_span'] = [min(start, tip), tip]
    # rail: fixed parts that begin where the slider ends (in the opening direction) and share its cross-section
    other = [i for i in range(3) if abs(a[i]) < .5]
    mlo, mhi = moving.min(0), moving.max(0); length = float(tm.max() - tm.min())
    rails, covered = [], []
    for row in fixed:
        pts = _group_points([row]); t = pts @ a
        across = all(pts[:, i].max() > mlo[i] and pts[:, i].min() < mhi[i] for i in other)
        if not across:
            continue
        covered.append((float(t.min()), float(t.max())))
        if abs(float(t.min()) - float(tm.max())) < length * .05 and float(t.max() - t.min()) > length * .5:
            rails.append(row)
    lo_t, hi_t = float(tm.min()), float(tm.max())
    bare = not any(start <= lo_t + length * .1 and end >= hi_t - length * .1 for start, end in covered)
    if rails and bare:
        joint['fill'] = [{'source': {k: v for k, v in row.items() if k in ('object', 'pieces')}, 'shift': -length,
                          'clip': [lo_t, hi_t]} for row in rails]


# ---------- pictures ----------

def _camera(points, azimuth, elevation, ortho, size):
    scene = bpy.context.scene
    scene.render.resolution_x = scene.render.resolution_y = size; scene.render.resolution_percentage = 100
    cam = bpy.data.objects.get('ArticulateCam')
    if cam is None:
        cam = bpy.data.objects.new('ArticulateCam', bpy.data.cameras.new('ArticulateCam')); scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.type = 'ORTHO' if ortho else 'PERSP'; cam.data.lens = 60; cam.data.clip_end = 1e6; cam.data.clip_start = 1e-4
    pts = np.array(points); lo, hi = pts.min(0), pts.max(0); target = Vector(((lo + hi) / 2).tolist()); reach = float(np.linalg.norm(hi - lo)) or 1
    az, el = math.radians(azimuth), math.radians(elevation)
    direction = Vector((math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)))
    cam.data.clip_start = reach * 1e-3; cam.data.clip_end = reach * 100
    def aim(distance):
        cam.location = target + direction * distance
        cam.rotation_euler = (target - cam.location).to_track_quat('-Z', 'Y').to_euler()
        bpy.context.view_layer.update()
    if ortho:
        aim(reach * 4)
        inverse = cam.matrix_world.inverted(); local = [inverse @ Vector(p) for p in pts[:: max(1, len(pts) // 4000)]]
        xs = [p.x for p in local]; ys = [p.y for p in local]
        cam.location += cam.matrix_world.to_3x3() @ Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0))
        cam.data.ortho_scale = max(max(xs) - min(xs), max(ys) - min(ys)) / .86
    else:
        sample = [Vector(p) for p in pts[:: max(1, len(pts) // 2000)]]
        low, high = reach * .05, reach * 40
        for _ in range(40):
            middle = (low + high) / 2; aim(middle)
            seen = [world_to_camera_view(scene, cam, p) for p in sample]
            if all(c.z > 0 and .07 <= c.x <= .93 and .07 <= c.y <= .93 for c in seen):
                high = middle
            else:
                low = middle
        aim(high)
    bpy.context.view_layer.update()
    return cam


def _workbench(colour):
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    shading = scene.display.shading
    shading.light = 'STUDIO'; shading.color_type = colour; shading.show_shadows = False; shading.show_cavity = True
    shading.cavity_type = 'WORLD'; shading.show_object_outline = True
    scene.render.film_transparent = False
    world = scene.world or bpy.data.worlds.new('World'); scene.world = world
    world.color = (1, 1, 1)
    scene.view_settings.view_transform = 'Standard'


def _colour_parts(parts):
    """Paint each part its own flat colour as a corner colour attribute (Workbench draws it)."""
    by_object = {}
    for part in parts:
        by_object.setdefault(part['object'], []).append(part)
    for name, rows in by_object.items():
        ob = bpy.data.objects[name]
        if ob.data.users > 1:
            ob.data = ob.data.copy()
        me = ob.data
        attr = me.color_attributes.get('articulate_parts') or me.color_attributes.new('articulate_parts', 'BYTE_COLOR', 'CORNER')
        me.color_attributes.active_color = attr
        loops = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get('vertex_index', loops)
        colours = np.ones((len(me.loops), 4))
        labels = pieces(ob) if any(row['pieces'] for row in rows) else None
        for row in rows:
            colour = PALETTE[(row['id'] - 1) % len(PALETTE)]
            mask = np.ones(len(loops), dtype=bool) if labels is None else np.isin(labels[loops], row['pieces'])
            colours[mask, :3] = colour
        attr.data.foreach_set('color', colours.reshape(-1))


def _render(path):
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def _label_points(parts, cam):
    scene = bpy.context.scene; out = {}
    for part in parts:
        co = world_to_camera_view(scene, cam, Vector(part['label_point']))
        out[part['id']] = [round(co.x, 4), round(1 - co.y, 4)]
    return out


def probe(request):
    out = Path(request['output_dir'])
    _import(request['model_path'])
    pts = np.concatenate([_world(ob) for ob in _mesh_objects()])
    longest = float(np.ptp(pts, axis=0).max()) or 1
    unit_mm = float(request['size_mm']) / longest if request.get('size_mm') else 1000.0
    parts = part_list(unit_mm)
    _colour_parts(parts); _workbench('VERTEX')
    views = []
    for name, az, el, ortho in (('top', 0, 90 - 1e-3, True), ('three_quarter', 35, 32, False)):
        cam = _camera(pts, az, el, ortho, int(request.get('size', 1024)))
        _render(out / (name + '.png'))
        views.append({'name': name, 'image': name + '.png', 'labels': _label_points(parts, cam)})
    objects = [{'name': ob.name, 'type': ob.type, 'parent': ob.parent.name if ob.parent else None,
                'origin_mm': (np.array(ob.matrix_world.translation) * unit_mm).round(1).tolist()} for ob in bpy.context.scene.objects
               if ob.name != 'ArticulateCam']
    for part in parts:
        part.pop('label_point', None)
    lo, hi = pts.min(0), pts.max(0)
    return {'success': True, 'parts': parts, 'objects': objects, 'views': views, 'unit_mm': unit_mm,
            'bbox_mm': [(lo * unit_mm).round(1).tolist(), (hi * unit_mm).round(1).tolist()]}


def _decal_hosts(parts):
    """{flat part id: id of the solid part it lies on}: a printed scale, a label, a legend has no thickness of its own and
    goes wherever the part under it goes, whatever the vision model said about it."""
    def box(part):
        return np.array(part['bbox_mm'][0]), np.array(part['bbox_mm'][1])
    longest = max(max(part['size_mm']) for part in parts) or 1
    solids = [part for part in parts if min(part['size_mm']) > longest * .01]
    hosts = {}
    for part in parts:
        if part in solids:
            continue
        lo, hi = box(part); thin = int(np.argmin(part['size_mm'])); plane = [i for i in range(3) if i != thin]
        centre = (lo + hi) / 2; best = None
        for solid in solids:
            slo, shi = box(solid); pad = longest * .01
            if all(slo[i] - pad <= lo[i] and hi[i] <= shi[i] + pad for i in plane):
                gap = max(0.0, slo[thin] - centre[thin], centre[thin] - shi[thin])
                area = np.prod([shi[i] - slo[i] for i in plane])
                if gap < longest * .02 and (best is None or (gap, area) < best[0]):
                    best = ((gap, area), solid['id'])
        if best:
            hosts[part['id']] = best[1]
    return hosts


def _follow_hosts(joint, hosts):
    """A joint with every flat part moved into its host's group (or out of all groups when its host does not move)."""
    joint = dict(joint); roles = ('moving', 'counter', 'stretch')
    for role in roles:
        joint[role] = [i for i in joint.get(role) or [] if i not in hosts]
    for decal, host in hosts.items():
        for role in roles:
            if host in joint[role]:
                joint[role].append(decal); break
    return joint


def _whole(rows, parts):
    """Selections merged per object; an object whose every part is selected becomes one whole-object selection (so its
    children follow it and it is treated as one body)."""
    by_object = {}
    for row in rows:
        by_object.setdefault(row['object'], []).append(row)
    out = []
    for name, picked in by_object.items():
        if any(not row.get('pieces') for row in picked):
            out.append({'object': name, 'pieces': None}); continue
        chosen = sorted({k for row in picked for k in row['pieces']})
        every = sorted({k for part in parts if part['object'] == name for k in (part['pieces'] or [])})
        out.append({'object': name, 'pieces': None if chosen == every else chosen})
    return out


def _selector(part):
    return {'object': part['object'], 'pieces': part['pieces']}


def solve(request):
    out = Path(request['output_dir'])
    _import(request['model_path'])
    pts = np.concatenate([_world(ob) for ob in _mesh_objects()])
    longest = float(np.ptp(pts, axis=0).max()) or 1
    unit_mm = float(request['size_mm']) / longest if request.get('size_mm') else 1000.0
    parts = part_list(unit_mm); by_id = {part['id']: part for part in parts}
    rows = [{**_selector(part), 'id': part['id']} for part in parts]
    hosts = _decal_hosts(parts)
    solved = []
    for joint in request.get('joints') or []:
        joint = _follow_hosts(joint, hosts)
        made = {'name': joint.get('name') or 'joint%d' % (len(solved) + 1), 'type': 'hinge' if joint.get('type') == 'hinge' else 'slide',
                'axis_name': joint.get('axis') or 'z', 'pivot_hint': joint.get('pivot') or {}, 'away_from': joint.get('away_from'),
                'max': joint.get('max')}
        for role in ('moving', 'counter', 'stretch'):
            made[role] = _whole([_selector(by_id[i]) for i in joint.get(role) or [] if i in by_id], parts)
        if made['moving']:
            solved.append(made)
    _detach_crossing_children(solved)          # a tape blade under its hook is its own group from here on
    solved = [solve_joint(joint, rows, unit_mm) for joint in solved]
    # previews: every state, one fixed camera framing all of them
    states = request.get('states') or []
    envelope = [pts]
    for state in states:
        snap = Snapshot(); apply({'joints': _valued(solved, state)})
        envelope.append(np.concatenate([_world(ob) for ob in _mesh_objects()])); snap.restore()
    allpts = np.concatenate(envelope)
    previews = []
    _workbench('TEXTURE')
    for index, state in enumerate(states):
        snap = Snapshot(); apply({'joints': _valued(solved, state)})
        _camera(allpts, 35, 32, False, int(request.get('size', 768)))
        name = 'state%d.png' % index; _render(out / name); snap.restore()
        previews.append({'state': state.get('id'), 'image': name})
    for joint in solved:
        joint.pop('pivot_hint', None)
    return {'success': True, 'joints': solved, 'previews': previews, 'unit_mm': unit_mm}


def _valued(joints, state):
    values = state.get('values') or {}
    return [{**joint, 'value': values.get(joint['name'], 0)} for joint in joints]


def _import(path):
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for item in list(block):
            if not item.users:
                block.remove(item)
    bpy.ops.import_scene.gltf(filepath=str(path), import_pack_images=True)
    bpy.context.view_layer.update()
    _PIECES.clear()


def main():
    request = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
    out = Path(request['output_dir']); out.mkdir(parents=True, exist_ok=True)
    try:
        result = probe(request) if request['action'] == 'probe' else solve(request)
    except Exception as exc:
        traceback.print_exc()
        result = {'success': False, 'error': str(exc), 'traceback': traceback.format_exc()}
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    if not result.get('success'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()

"""SDK-independent invariants. These tests do NOT claim native C4D verification."""
import importlib.util
import json
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("scene_converter", ROOT / "c4d" / "convert_scene.py")
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


def transform(matrix, point):
    return [sum(matrix[r][c] * point[c] for c in range(3)) + matrix[r][3] for r in range(3)]


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


class CoordinateTests(unittest.TestCase):
    def test_world_positions_survive_nonuniform_transform(self):
        matrix = [[0, -2, 0, 1], [3, 0, 0, 2], [0, 0, 4, 3], [0, 0, 0, 1]]
        converted = converter.converted_matrix(matrix)
        for point in ([1, 2, 3], [-4, 6, 1], [0, 0, 0]):
            expected = converter.converted_point(transform(matrix, point))
            actual = transform(converted, converter.converted_point(point))
            for a, b in zip(expected, actual):
                self.assertAlmostEqual(a, b)

    def test_camera_forward_and_up(self):
        matrix = [[1, 0, 0, 1], [0, 0, -1, 2], [0, 1, 0, 3], [0, 0, 0, 1]]
        converted = converter.converted_matrix(matrix, directional=True)
        # C4D +Z forward equals reflected Blender -Z, and camera up stays up.
        for c4d_local, blender_local in (([0, 0, 1], [0, 0, -1]), ([0, 1, 0], [0, 1, 0])):
            expected = converter.converted_point(transform(matrix, blender_local))
            actual = transform(converted, [v*100 for v in c4d_local])
            self.assertEqual(list(expected), actual)

    def test_outward_normal_and_corner_uvs_survive(self):
        points = [[0, 0, 0], [1, 0, 0], [1, 1, 0]]
        indices, uvs = converter.converted_face([0, 1, 2], [[0, 0], [1, 0], [1, 1]])
        transformed = [converter.converted_point(points[i], 1) for i in indices[:3]]
        a, b, c = transformed
        normal = cross([b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)])
        self.assertEqual(normal, [0, 1, 0])
        self.assertEqual(uvs, [(0, 1, 0), (1, 0, 0), (1, 1, 0), (1, 1, 0)])

    def test_ngons_rejected_instead_of_invalid_fan(self):
        with self.assertRaises(ValueError):
            converter.converted_face([0, 1, 2, 3, 4])

    def test_hierarchy_parent_first(self):
        items = [{"name": "child", "parent": "parent"}, {"name": "parent", "parent": "root"}, {"name": "root"}]
        self.assertEqual([x["name"] for x in converter.ordered_objects(items)], ["root", "parent", "child"])

    def test_cycles_and_missing_parent_fail(self):
        for items in ([{"name": "a", "parent": "b"}], [{"name": "a", "parent": "b"}, {"name": "b", "parent": "a"}]):
            with self.assertRaises(ValueError):
                converter.ordered_objects(items)

    def test_real_exchange_mesh_corner_alignment(self):
        paths = list((ROOT / "validation" / "scene-export").glob("*/scene.json"))
        self.assertGreaterEqual(len(paths), 2)
        for path in paths:
            scene = json.loads(path.read_text(encoding="utf-8"))
            for item in converter.ordered_objects(scene["objects"]):
                self.assertTrue(all(math.isfinite(x) for row in converter.converted_matrix(item["matrix_world"]) for x in row))
                mesh = item.get("mesh")
                if not mesh:
                    continue
                uvs = mesh.get("uvs", [])
                self.assertEqual(len(mesh["material_indices"]), len(mesh["faces"]))
                for n, face in enumerate(mesh["faces"]):
                    indices, converted_uv = converter.converted_face(face, uvs[n] if uvs else None)
                    self.assertTrue(all(0 <= i < len(mesh["vertices"]) for i in indices))
                    if uvs:
                        self.assertEqual(len(converted_uv), 4)

    def test_no_sdk_never_claims_success(self):
        original = converter.c4d
        converter.c4d = None
        try:
            with self.assertRaisesRegex(RuntimeError, "SDK unavailable"):
                converter.Converter("scene.json", "scene.c4d").convert()
        finally:
            converter.c4d = original


if __name__ == "__main__":
    unittest.main()

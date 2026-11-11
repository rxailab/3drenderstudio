# -*- coding: utf-8 -*-
"""Convert the app's scene.json to a genuine, editable Cinema 4D document.

Run with a licensed R24+ c4dpy:
    c4dpy convert_scene.py scene.json scene.c4d result.json
Or open this file in Cinema 4D's Script Manager and run it. It first looks
for scene.json beside the script, otherwise asks for the exchange file.
The GUI path creates a NEW document and never edits the user's open document.

SDK references (also checked against installed R24 description headers):
https://developers.maxon.net/docs/py/2024_5_0/modules/c4d.documents/index.html
https://developers.maxon.net/docs/py/2024_4_0a/modules/c4d/C4DAtom/GeListNode/BaseList2D/BaseMaterial/Material/index.html
https://developers.maxon.net/docs/py/2024_4_0a/modules/c4d/C4DAtom/GeListNode/BaseList2D/BaseTag/VariableTag/UVWTag/index.html

This is an interchange conversion, not a Cycles renderer recreation. Meshes,
hierarchy, UVs, material assignments, cameras and lights remain editable.
PBR material/light responses, world illumination, color management, custom
normals, modifiers and procedural nodes are explicitly approximate or baked.
"""
from __future__ import print_function

import hashlib
import json
import math
import os
import re
import shutil
import sys
import traceback

try:
    import c4d
    if not hasattr(c4d, "BaseObject"):
        c4d = None
except ImportError:
    c4d = None


# Geometry is stored in centimeters in a document with a 1 cm unit scale.
UNIT_SCALE = 100.0
IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
LIMITATIONS = [
    "Cinema 4D Standard materials approximate Blender Principled BSDF; renderer output is not pixel-identical.",
    "Light energy is mapped heuristically from Blender watts to C4D brightness; original values remain in User Data.",
    "Modifiers are evaluated into editable polygons. Animation, constraints, procedural shader graphs and custom normals are not reconstructed.",
    "Blender color management and world illumination are not identical in Cinema 4D; world color/texture becomes a luminous Sky.",
]


def converted_point(point, scale=UNIT_SCALE):
    """Blender RH Z-up -> C4D LH Y-up, using the reflection (x,z,y)."""
    return (float(point[0]) * scale, float(point[2]) * scale,
            float(point[1]) * scale)


def converted_matrix(matrix, directional=False):
    """Return row-major C4D world transform, with centimeter translation.

    Geometry uses C M C^-1. Camera/light local right/up/forward axes instead
    use C M diag(1,1,-1), because Blender faces -Z and C4D faces +Z.
    """
    rows = (0, 2, 1)
    result = [list(row) for row in IDENTITY]
    for row, source_row in enumerate(rows):
        for col in range(3):
            source_col = col if directional else rows[col]
            sign = -1.0 if directional and col == 2 else 1.0
            result[row][col] = float(matrix[source_row][source_col]) * sign
        result[row][3] = float(matrix[source_row][3]) * UNIT_SCALE
    return result


def converted_face(face, uv=None):
    """Reverse winding after the coordinate reflection; keep UV corners paired."""
    if len(face) not in (3, 4):
        raise ValueError("Only triangles/quads supported; tessellate n-gons in Blender first")
    order = [0] + list(range(len(face) - 1, 0, -1))
    indices = [int(face[i]) for i in order]
    coordinates = None
    if uv is not None:
        if len(uv) != len(face):
            raise ValueError("UV corner count differs from polygon corner count")
        # Blender image UV origin is bottom-left; C4D bitmap UV origin is top-left.
        coordinates = [(float(uv[i][0]), 1.0 - float(uv[i][1]), 0.0) for i in order]
    if len(indices) == 3:
        indices.append(indices[-1])
        if coordinates is not None:
            coordinates.append(coordinates[-1])
    return indices, coordinates


def ordered_objects(objects):
    """Parents must be transformed before their children receive world matrices."""
    by_name = {}
    for obj in objects:
        if not obj.get("name") or obj["name"] in by_name:
            raise ValueError("Object names must be non-empty and unique")
        by_name[obj["name"]] = obj
    result, done, visiting = [], set(), set()

    def visit(name):
        if name in done:
            return
        if name in visiting:
            raise ValueError("Object hierarchy contains a cycle: " + name)
        visiting.add(name)
        parent = by_name[name].get("parent")
        if parent:
            if parent not in by_name:
                raise ValueError("Missing parent: " + parent)
            visit(parent)
        visiting.remove(name)
        done.add(name)
        result.append(by_name[name])

    for name in by_name:
        visit(name)
    return result


def vec(value):
    return c4d.Vector(*[float(x) for x in value[:3]])


def sdk_matrix(matrix):
    return c4d.Matrix(off=vec([matrix[i][3] for i in range(3)]),
                      v1=vec([matrix[i][0] for i in range(3)]),
                      v2=vec([matrix[i][1] for i in range(3)]),
                      v3=vec([matrix[i][2] for i in range(3)]))


def metadata(node, label, value):
    description = c4d.GetCustomDataTypeDefault(c4d.DTYPE_STRING)
    description[c4d.DESC_NAME] = label
    description[c4d.DESC_ANIMATE] = c4d.DESC_ANIMATE_OFF
    key = node.AddUserData(description)
    node[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)


class Converter(object):
    def __init__(self, scene_path, output_path):
        self.scene_path = os.path.abspath(scene_path)
        self.output_path = os.path.abspath(output_path)
        self.source_dir = os.path.realpath(os.path.dirname(self.scene_path))
        self.output_dir = os.path.dirname(self.output_path)
        self.warnings = list(LIMITATIONS)
        self.materials = {}
        self.objects = {}
        self.texture_files = set()
        self.doc = None

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def bitmap(self, material, texture):
        if not texture:
            return None
        path = texture.get("path") if isinstance(texture, dict) else texture
        if not path:
            return None
        source = os.path.realpath(os.path.join(self.source_dir, path))
        # Packages only contain local assets. Do not load arbitrary paths/URLs.
        try:
            inside = os.path.normcase(os.path.commonpath([self.source_dir, source])) == os.path.normcase(self.source_dir)
        except ValueError:
            inside = False
        if not inside:
            self.warn("Texture outside exchange directory skipped: " + str(path))
            return None
        if not os.path.isfile(source):
            self.warn("Missing texture: " + str(path))
            return None
        tex_dir = os.path.join(self.output_dir, "tex")
        if not os.path.isdir(tex_dir):
            os.makedirs(tex_dir)
        name = re.sub(r"[^a-zA-Z0-9_.-]", "_", os.path.basename(source))
        # Stable names, no accidental overwrite when two sources share a basename.
        digest = hashlib.sha256()
        with open(source, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        name = digest.hexdigest()[:12] + "_" + name
        target = os.path.join(tex_dir, name)
        if os.path.normcase(source) != os.path.normcase(target):
            shutil.copy2(source, target)
        self.texture_files.add("tex/" + name)
        shader = c4d.BaseShader(c4d.Xbitmap)
        if shader is None:
            raise MemoryError("Could not create bitmap shader")
        shader[c4d.BITMAPSHADER_FILENAME] = "tex/" + name
        colorspace = texture.get("colorspace", "sRGB") if isinstance(texture, dict) else "sRGB"
        shader[c4d.BITMAPSHADER_COLORPROFILE] = (
            c4d.BITMAPSHADER_COLORPROFILE_LINEAR if colorspace.lower() in
            ("non-color", "non-color data", "linear", "raw") else
            c4d.BITMAPSHADER_COLORPROFILE_SRGB)
        material.InsertShader(shader)
        return shader

    def material(self, item):
        mat = c4d.BaseMaterial(c4d.Mmaterial)
        if mat is None:
            raise MemoryError("Could not create material")
        mat.SetName(item["name"])
        self.doc.InsertMaterial(mat)
        color = item.get("base_color", [0.8, 0.8, 0.8, 1.0])
        roughness = max(0.0, min(1.0, float(item.get("roughness", 0.5))))
        metallic = max(0.0, min(1.0, float(item.get("metallic", 0.0))))
        transmission = max(0.0, min(1.0, float(item.get("transmission", 0.0))))
        textures = item.get("textures", {})
        for warning in item.get("warnings", []):
            self.warn(item["name"] + ": " + str(warning))
        mat[c4d.MATERIAL_USE_COLOR] = metallic < 1.0
        mat[c4d.MATERIAL_COLOR_COLOR] = vec(color)
        mat[c4d.MATERIAL_COLOR_BRIGHTNESS] = 1.0 - metallic
        color_shader = self.bitmap(mat, textures.get("base_color"))
        if color_shader:
            mat[c4d.MATERIAL_COLOR_SHADER] = color_shader
        mat.RemoveReflectionAllLayers()
        mat[c4d.MATERIAL_USE_REFLECTION] = True
        layer = mat.AddReflectionLayer()
        if layer is None:
            raise MemoryError("Could not create reflectance layer")
        layer.SetName("Principled approximation")
        layer.SetFlags(c4d.REFLECTION_FLAG_ACTIVE | c4d.REFLECTION_FLAG_TAB)
        base = layer.GetDataID()
        mat[base + c4d.REFLECTION_LAYER_MAIN_DISTRIBUTION] = c4d.REFLECTION_DISTRIBUTION_GGX
        mat[base + c4d.REFLECTION_LAYER_MAIN_VALUE_ROUGHNESS] = roughness
        mat[base + c4d.REFLECTION_LAYER_MAIN_VALUE_REFLECTION] = 1.0
        mat[base + c4d.REFLECTION_LAYER_MAIN_VALUE_SPECULAR] = 1.0
        mat[base + c4d.REFLECTION_LAYER_COLOR_COLOR] = vec(color if metallic >= 0.5 else [1, 1, 1])
        mat[base + c4d.REFLECTION_LAYER_FRESNEL_MODE] = (
            c4d.REFLECTION_FRESNEL_CONDUCTOR if metallic >= 0.5 else c4d.REFLECTION_FRESNEL_DIELECTRIC)
        mat[base + c4d.REFLECTION_LAYER_FRESNEL_VALUE_IOR] = float(item.get("ior", 1.45))
        for channel, parameter in (("roughness", c4d.REFLECTION_LAYER_MAIN_SHADER_ROUGHNESS),):
            shader = self.bitmap(mat, textures.get(channel))
            if shader:
                mat[base + parameter] = shader
        if metallic >= 0.5:
            shader = self.bitmap(mat, textures.get("base_color"))
            if shader:
                mat[base + c4d.REFLECTION_LAYER_COLOR_TEXTURE] = shader
        if "metallic" in textures:
            self.bitmap(mat, textures["metallic"])
            self.warn(item["name"] + ": metallic texture is preserved in exchange assets but C4D uses the scalar metallic approximation.")
        if transmission > 0 or "transmission" in textures:
            mat[c4d.MATERIAL_USE_TRANSPARENCY] = True
            mat[c4d.MATERIAL_TRANSPARENCY_COLOR] = vec(color)
            mat[c4d.MATERIAL_TRANSPARENCY_BRIGHTNESS] = transmission
            mat[c4d.MATERIAL_TRANSPARENCY_REFRACTION] = float(item.get("ior", 1.45))
            mat[c4d.MATERIAL_TRANSPARENCY_FRESNEL] = True
            shader = self.bitmap(mat, textures.get("transmission"))
            if shader:
                mat[c4d.MATERIAL_TRANSPARENCY_SHADER] = shader
        emission = item.get("emission", [0, 0, 0, 1])
        emission_strength = float(item.get("emission_strength", 0.0))
        if (emission_strength > 0 and max(emission[:3]) > 0) or "emission" in textures:
            mat[c4d.MATERIAL_USE_LUMINANCE] = True
            mat[c4d.MATERIAL_LUMINANCE_COLOR] = vec(emission)
            mat[c4d.MATERIAL_LUMINANCE_BRIGHTNESS] = emission_strength
            shader = self.bitmap(mat, textures.get("emission"))
            if shader:
                mat[c4d.MATERIAL_LUMINANCE_SHADER] = shader
        for channel, enable, parameter in (
            ("normal", c4d.MATERIAL_USE_NORMAL, c4d.MATERIAL_NORMAL_SHADER),
            ("alpha", c4d.MATERIAL_USE_ALPHA, c4d.MATERIAL_ALPHA_SHADER),
            ("bump", c4d.MATERIAL_USE_BUMP, c4d.MATERIAL_BUMP_SHADER)):
            shader = self.bitmap(mat, textures.get(channel))
            if shader:
                mat[enable], mat[parameter] = True, shader
        if len(color) > 3 and color[3] < 1.0 and "alpha" not in textures:
            self.warn(item["name"] + ": scalar base-color alpha is recorded but not reproduced by the Standard material.")
        metadata(mat, "Source material parameters", item)
        mat.Message(c4d.MSG_UPDATE)
        self.materials[item["name"]] = mat

    def mesh(self, item):
        data = item["mesh"]
        points, faces = data["vertices"], data["faces"]
        obj = c4d.PolygonObject(len(points), len(faces))
        obj.SetAllPoints([vec(converted_point(point)) for point in points])
        uvs = data.get("uvs", [])
        if uvs and len(uvs) != len(faces):
            raise ValueError("UV polygon count does not match " + item["name"])
        uv_tag = c4d.UVWTag(len(faces)) if uvs else None
        for index, face in enumerate(faces):
            if any(not isinstance(v, int) or v < 0 or v >= len(points) for v in face):
                raise ValueError("Invalid polygon vertex index in " + item["name"])
            indices, coordinates = converted_face(face, uvs[index] if uvs else None)
            obj.SetPolygon(index, c4d.CPolygon(*indices))
            if uv_tag:
                uv_tag.SetSlow(index, *[vec(uv) for uv in coordinates])
        if uv_tag:
            obj.InsertTag(uv_tag)
        # The evaluated mesh remains editable; a Phong tag approximates smooth normals.
        phong = c4d.BaseTag(c4d.Tphong)
        phong[c4d.PHONGTAG_PHONG_ANGLELIMIT] = True
        phong[c4d.PHONGTAG_PHONG_ANGLE] = math.radians(60)
        obj.InsertTag(phong)
        slots = data.get("materials", item.get("materials", []))
        face_materials = data.get("material_indices", [])
        if face_materials and len(face_materials) != len(faces):
            raise ValueError("Material polygon count does not match " + item["name"])
        for slot, name in enumerate(slots):
            mat = self.materials.get(name)
            if mat is None:
                if name:
                    self.warn("Unresolved material on " + item["name"] + ": " + str(name))
                continue
            selection = c4d.SelectionTag(c4d.Tpolygonselection)
            selection.SetName("Material_%03d" % slot)
            selected = selection.GetBaseSelect()
            for index in range(len(faces)):
                if (face_materials[index] if face_materials else 0) == slot:
                    selected.Select(index)
            obj.InsertTag(selection)
            tag = c4d.TextureTag()
            tag[c4d.TEXTURETAG_MATERIAL] = mat
            tag[c4d.TEXTURETAG_PROJECTION] = c4d.TEXTURETAG_PROJECTION_UVW
            tag[c4d.TEXTURETAG_RESTRICTION] = selection.GetName()
            obj.InsertTag(tag)
        obj.Message(c4d.MSG_UPDATE)
        return obj

    def camera(self, item):
        data = item["camera"]
        obj = c4d.BaseObject(c4d.Ocamera)
        obj[c4d.CAMERA_FOCUS] = float(data.get("lens", 50))
        obj[c4d.CAMERAOBJECT_APERTURE] = float(data.get("sensor_width", 36))
        obj[c4d.CAMERAOBJECT_NEAR_CLIPPING_ENABLE] = True
        obj[c4d.CAMERAOBJECT_NEAR_CLIPPING] = float(data.get("clip_start", 0.01)) * UNIT_SCALE
        obj[c4d.CAMERAOBJECT_FAR_CLIPPING_ENABLE] = True
        obj[c4d.CAMERAOBJECT_FAR_CLIPPING] = float(data.get("clip_end", 1000)) * UNIT_SCALE
        obj[c4d.CAMERAOBJECT_TARGETDISTANCE] = float(data.get("focus_distance", 10)) * UNIT_SCALE
        obj[c4d.CAMERAOBJECT_FNUMBER_VALUE] = float(data.get("fstop", 5.6))
        obj[c4d.CAMERAOBJECT_FILM_OFFSET_X] = float(data.get("shift_x", 0))
        obj[c4d.CAMERAOBJECT_FILM_OFFSET_Y] = float(data.get("shift_y", 0))
        if data.get("type") == "ORTHO":
            obj[c4d.CAMERA_PROJECTION] = c4d.Pparallel
            obj[c4d.CAMERA_ZOOM] = 1024.0 / max(1e-6, float(data.get("ortho_scale", 6)) * UNIT_SCALE)
        elif data.get("type", "PERSP") != "PERSP":
            self.warn(item["name"] + ": panoramic camera converted to perspective.")
        metadata(obj, "Source camera parameters", data)
        return obj

    def light(self, item):
        data = item["light"]
        obj = c4d.BaseObject(c4d.Olight)
        kind = data.get("type", "POINT")
        kinds = {"AREA": c4d.LIGHT_TYPE_AREA, "SUN": c4d.LIGHT_TYPE_DISTANT,
                 "SPOT": c4d.LIGHT_TYPE_SPOT, "POINT": c4d.LIGHT_TYPE_OMNI}
        obj[c4d.LIGHT_TYPE] = kinds.get(kind, c4d.LIGHT_TYPE_OMNI)
        obj[c4d.LIGHT_COLOR] = vec(data.get("color", [1, 1, 1]))
        energy = max(0.0, float(data.get("energy", 100)))
        obj[c4d.LIGHT_BRIGHTNESS] = energy if kind == "SUN" else energy / 1000.0
        obj[c4d.LIGHT_SHADOWTYPE] = c4d.LIGHT_SHADOWTYPE_AREA
        if kind == "AREA":
            obj[c4d.LIGHT_AREADETAILS_SIZEX] = float(data.get("size", 1)) * UNIT_SCALE
            obj[c4d.LIGHT_AREADETAILS_SIZEY] = float(data.get("size_y", data.get("size", 1))) * UNIT_SCALE
            obj[c4d.LIGHT_AREADETAILS_SHAPE] = (c4d.LIGHT_AREADETAILS_SHAPE_DISC
                if data.get("shape") in ("DISK", "ELLIPSE") else c4d.LIGHT_AREADETAILS_SHAPE_RECTANGLE)
            obj[c4d.LIGHT_AREADETAILS_SHOWINREFLECTION] = True
        if kind == "SPOT":
            angle = float(data.get("spot_size", math.radians(45)))
            obj[c4d.LIGHT_DETAILS_OUTERANGLE] = angle
            obj[c4d.LIGHT_DETAILS_INNERANGLE] = angle * (1.0 - float(data.get("spot_blend", 0.15)))
        metadata(obj, "Source light parameters", data)
        return obj

    def world(self, data):
        if not data:
            return
        mat = c4d.BaseMaterial(c4d.Mmaterial)
        mat.SetName("World environment")
        mat[c4d.MATERIAL_USE_COLOR] = False
        mat[c4d.MATERIAL_USE_REFLECTION] = False
        mat[c4d.MATERIAL_USE_LUMINANCE] = True
        mat[c4d.MATERIAL_LUMINANCE_COLOR] = vec(data.get("color", [0.05, 0.05, 0.05]))
        mat[c4d.MATERIAL_LUMINANCE_BRIGHTNESS] = float(data.get("strength", 1))
        self.doc.InsertMaterial(mat)
        textures = data.get("textures", {})
        texture = textures.get("environment") or textures.get("color") or data.get("texture")
        shader = self.bitmap(mat, texture)
        if shader:
            mat[c4d.MATERIAL_LUMINANCE_SHADER] = shader
        sky = c4d.BaseObject(c4d.Osky)
        sky.SetName("World environment")
        self.doc.InsertObject(sky)
        tag = c4d.TextureTag()
        tag[c4d.TEXTURETAG_MATERIAL] = mat
        tag[c4d.TEXTURETAG_PROJECTION] = c4d.TEXTURETAG_PROJECTION_SPHERICAL
        sky.InsertTag(tag)
        metadata(sky, "Source world parameters", data)

    def build(self, scene):
        if scene.get("schema_version") != 1 or scene.get("axis") != "blender_z_up" or scene.get("units") != "meters":
            raise ValueError("Expected scene schema 1, meters and blender_z_up")
        self.doc = c4d.documents.BaseDocument()
        self.doc.ForceCreateBaseDraw()
        self.doc.SetDocumentPath(self.output_dir)
        self.doc.SetDocumentName(os.path.basename(self.output_path))
        scale = c4d.UnitScaleData()
        scale.SetUnitScale(1.0, c4d.DOCUMENT_UNIT_CM)
        self.doc[c4d.DOCUMENT_DOCUNIT] = scale
        for warning in scene.get("warnings", []):
            self.warn(str(warning))
        for material in scene.get("materials", []):
            self.material(material)
        for item in ordered_objects(scene.get("objects", [])):
            if "mesh" in item:
                obj = self.mesh(item)
            elif "camera" in item:
                obj = self.camera(item)
            elif "light" in item:
                obj = self.light(item)
            else:
                obj = c4d.BaseObject(c4d.Onull)
            obj.SetName(item["name"])
            self.doc.InsertObject(obj, parent=self.objects.get(item.get("parent")))
            directional = "camera" in item or "light" in item
            obj.SetMg(sdk_matrix(converted_matrix(item.get("matrix_world", IDENTITY), directional)))
            if item.get("hide_render"):
                obj.SetRenderMode(c4d.MODE_OFF)
            if item.get("hide_viewport"):
                obj.SetEditorMode(c4d.MODE_OFF)
            self.objects[item["name"]] = obj
        settings = scene.get("render", {})
        fps = max(1, int(settings.get("fps", 24)))
        self.doc.SetFps(fps)
        self.doc.SetTime(c4d.BaseTime(int(settings.get("frame", 1)), fps))
        render = self.doc.GetActiveRenderData()
        percentage = float(settings.get("resolution_percentage", 100)) / 100.0
        render[c4d.RDATA_XRES] = max(1, int(settings.get("width", 1920) * percentage))
        render[c4d.RDATA_YRES] = max(1, int(settings.get("height", 1080) * percentage))
        render[c4d.RDATA_FRAMERATE] = fps
        if "pixel_aspect_x" in settings:
            render[c4d.RDATA_PIXELASPECT] = float(settings["pixel_aspect_x"]) / float(settings.get("pixel_aspect_y", 1))
        elif "pixel_aspect" in settings:
            render[c4d.RDATA_PIXELASPECT] = float(settings["pixel_aspect"][0]) / float(settings["pixel_aspect"][1])
        camera_name = settings.get("active_camera") or settings.get("camera") or scene.get("active_camera")
        active = self.objects.get(camera_name)
        if active is None:
            active = next((obj for obj in self.objects.values() if obj.CheckType(c4d.Ocamera)), None)
        if active is not None:
            self.doc.GetRenderBaseDraw().SetSceneCamera(active)
        self.world(scene.get("world", {}))
        metadata(self.doc, "Scene export information", {"plan": scene.get("plan"), "shot": scene.get("shot"),
                 "source_render": settings, "conversion_warnings": self.warnings})
        self.doc.ExecutePasses(None, True, True, True, c4d.BUILDFLAGS_NONE)

    def convert(self, display=False):
        if c4d is None:
            raise RuntimeError("Cinema 4D Python SDK unavailable. Run this script with licensed c4dpy or Cinema 4D Script Manager.")
        if not self.output_path.lower().endswith(".c4d"):
            raise ValueError("Output filename must end in .c4d")
        with open(self.scene_path, "r", encoding="utf-8-sig") as handle:
            scene = json.load(handle)
        if not os.path.isdir(self.output_dir):
            os.makedirs(self.output_dir)
        self.build(scene)
        expected = inspect_document(self.doc)
        saved = c4d.documents.SaveDocument(self.doc, self.output_path,
            c4d.SAVEDOCUMENTFLAGS_DONTADDTORECENTLIST, c4d.FORMAT_C4DEXPORT)
        if not saved or not os.path.isfile(self.output_path) or os.path.getsize(self.output_path) == 0:
            raise RuntimeError("Cinema 4D SaveDocument failed")
        reopened = c4d.documents.LoadDocument(self.output_path,
            c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS, None)
        if reopened is None:
            raise RuntimeError("Saved C4D document could not be reopened")
        verified = inspect_document(reopened)
        if verified != expected:
            raise RuntimeError("Reopened scene differs from saved document: " + json.dumps({"expected": expected, "actual": verified}))
        for relative in self.texture_files:
            if not os.path.isfile(os.path.join(self.output_dir, relative)):
                raise RuntimeError("Missing bundled texture: " + relative)
        if display:
            c4d.documents.InsertBaseDocument(reopened)
            c4d.documents.SetActiveDocument(reopened)
            c4d.EventAdd()
        return {"success": True, "error": None, "output": self.output_path,
                "warnings": self.warnings, "verified": verified,
                "verification": "Cinema 4D SaveDocument followed by LoadDocument and structure comparison",
                "textures": sorted(self.texture_files), "c4d_version": c4d.GetC4DVersion()}


def inspect_document(document):
    counts = {"objects": 0, "meshes": 0, "cameras": 0, "lights": 0,
              "materials": 0, "vertices": 0, "polygons": 0, "uv_tags": 0, "material_tags": 0}
    objects = []

    def walk(obj):
        while obj:
            counts["objects"] += 1
            entry = {"name": obj.GetName(), "type": obj.GetType(),
                     "parent": obj.GetUp().GetName() if obj.GetUp() else None}
            if obj.CheckType(c4d.Opolygon):
                counts["meshes"] += 1
                counts["vertices"] += obj.GetPointCount()
                counts["polygons"] += obj.GetPolygonCount()
            if obj.CheckType(c4d.Ocamera):
                counts["cameras"] += 1
            if obj.CheckType(c4d.Olight):
                counts["lights"] += 1
            for tag in obj.GetTags():
                if tag.CheckType(c4d.Tuvw):
                    counts["uv_tags"] += 1
                if tag.CheckType(c4d.Ttexture):
                    counts["material_tags"] += 1
            objects.append(entry)
            walk(obj.GetDown())
            obj = obj.GetNext()

    walk(document.GetFirstObject())
    material = document.GetFirstMaterial()
    while material:
        counts["materials"] += 1
        material = material.GetNext()
    camera = document.GetRenderBaseDraw().GetSceneCamera(document)
    counts["active_camera"] = camera.GetName() if camera else None
    counts["hierarchy"] = objects
    return counts


def main():
    args = sys.argv[1:]
    # Script Manager may inherit Cinema 4D's process arguments. Only accept the
    # explicit exchange-file invocation, never treat unrelated app flags as paths.
    cli = len(args) >= 2 and args[0].lower().endswith(".json") and args[1].lower().endswith(".c4d")
    if cli:
        scene_path, output_path = args[:2]
        result_path = args[2] if len(args) > 2 else os.path.splitext(output_path)[0] + ".result.json"
    else:
        if c4d is None:
            raise RuntimeError("Usage: c4dpy convert_scene.py scene.json scene.c4d result.json")
        script_dir = os.path.dirname(os.path.abspath(globals().get("__file__", "convert_scene.py")))
        scene_path = os.path.join(script_dir, "scene.json")
        if not os.path.isfile(scene_path):
            scene_path = c4d.storage.LoadDialog(title="Select exported scene.json", flags=c4d.FILESELECT_LOAD)
        if not scene_path:
            return
        output_path = os.path.join(os.path.dirname(scene_path), "scene.c4d")
        if os.path.exists(output_path):
            output_path = c4d.storage.SaveDialog(title="Save converted Cinema 4D scene", force_suffix="c4d")
            if not output_path:
                return
        result_path = os.path.splitext(output_path)[0] + ".result.json"
    converter = Converter(scene_path, output_path)
    try:
        result = converter.convert(display=not cli)
    except Exception as error:
        result = {"success": False, "error": str(error), "warnings": converter.warnings,
                  "verified": None, "traceback": traceback.format_exc()}
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=True))
    if not cli:
        c4d.gui.MessageDialog("Saved and reopened: " + output_path if result["success"] else "Conversion failed: " + result["error"])
    if not result["success"] and cli:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

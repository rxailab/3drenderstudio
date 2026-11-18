"""A clear reflection of the product on the floor it stands on.

Grey and dark studio floors are real objects: they are given a glossy coat, and Cycles
renders the reflection itself.

A white background is a shadow catcher, which carries shadows but not reflections, and a
glossy white floor would turn grey wherever it reflects the dim room. A light grey floor has
the same trouble: it reflects a room as dim as the product, so the reflection hardly shows.
So white and grey renders are made on transparent film over a shadow-catching floor and
finished here: laid on their ground (white, or the grey studio gradient) with their shadow,
and with the product's reflection under it, fading with distance from where it meets the floor.

The reflection is rendered (``mirror_pass``): the product and its lights are copied and flipped
about the floor, the originals and the floor are hidden, and the copies are rendered from the
same camera on transparent film. That is what the floor would show, in true perspective. (The
older stand-in, mirroring each pixel column down from the product's lowest pixel, is only used
when that pass cannot be made: in a three-quarter view it flips the faces seen from above and its
stepped contact line doubles thin lines such as stitching, which reads as a ghost image.) Only
numpy is needed, which Blender ships with; the images are read and written with bpy.
"""
import numpy

STRENGTH = .5              # opacity of the reflection where it meets the product
MIRROR_STRENGTH = .38      # opacity of a rendered (mirror-pass) reflection where it meets the product: acrylic, not a mirror
LENGTH = .55               # how far it reaches, as a share of the product's height in that column
SOLID = .97                # alpha above which a pixel is the product, not its shadow
SMOOTH = 9                 # columns over which the product's lowest edge is smoothed


def glossy(material, dark=False):
    """Give a visible studio floor a clear coat that reflects the product."""
    from blender.worker import bsdf_of
    shader = bsdf_of(material)
    if shader is None:
        return False
    shader.inputs['Roughness'].default_value = .2 if dark else .35
    for name, value in (('Coat Weight', 1.0), ('Coat Roughness', .02 if dark else .03)):
        socket = shader.inputs.get(name)
        if socket is not None:
            socket.default_value = value
    # A coat of ordinary lacquer reflects only a few per cent at a low camera angle, too little to see on a light floor.
    # A denser coat (a higher index) reflects about a fifth: a clear reflection, still clearly a floor and not a mirror.
    index = shader.inputs.get('Coat IOR')
    if index is not None:
        index.default_value = 1.9 if dark else 2.6
    specular = shader.inputs.get('Specular IOR Level')
    if specular is not None:
        specular.default_value = .7
    return True


def _lowest(solid):
    """Per column: the row of the product's lowest pixel (-1 where the column has none)."""
    height = solid.shape[0]
    present = solid.any(axis=0)
    lowest = height - 1 - numpy.argmax(solid[::-1, :], axis=0)
    highest = numpy.argmax(solid, axis=0)
    lowest = numpy.where(present, lowest, -1); highest = numpy.where(present, highest, -1)
    return present, lowest, highest


def _smoothed(values, present, width):
    """A running median of the lowest edge across neighbouring columns, so the reflection does not tear at jagged outlines."""
    if width < 3 or present.sum() < width:
        return values
    result = values.copy(); half = width // 2; columns = numpy.nonzero(present)[0]
    for index, column in enumerate(columns):
        window = columns[max(0, index - half):index + half + 1]
        window = window[numpy.abs(window - column) <= half]
        result[column] = int(numpy.median(values[window]))
    return result


GROUNDS = ('white', 'soft')
# The grey studio gradient as the lit floor rendered it, top to bottom (display values), and where it levels off.
SOFT_TOP = numpy.array([103, 108, 113], dtype=numpy.float32) / 255
SOFT_BOTTOM = numpy.array([189, 192, 195], dtype=numpy.float32) / 255
SOFT_LEVEL = .8


def ground(kind, height, width):
    """The background a finished render is laid on: white, or the grey studio gradient."""
    if kind == 'soft':
        t = numpy.clip(numpy.linspace(0, 1, height, dtype=numpy.float32) / SOFT_LEVEL, 0, 1)
        t = t * t * (3 - 2 * t)
        column = SOFT_TOP[None, :] + (SOFT_BOTTOM - SOFT_TOP)[None, :] * t[:, None]
        return numpy.broadcast_to(column[:, None, :], (height, width, 3)).copy()
    return numpy.ones((height, width, 3), dtype=numpy.float32)


def onto_white(rgba, strength=STRENGTH, length=LENGTH, kind='white'):
    """A float RGBA image (rows from the top, straight alpha, display values 0-1) laid on its ground with the product's reflection.

    Returns (RGB array, report). The shadow in the alpha is kept; the reflection is added below the product only.
    """
    rgb = rgba[..., :3]; alpha = rgba[..., 3:4]
    out = rgb * alpha + ground(kind, rgba.shape[0], rgba.shape[1]) * (1.0 - alpha)
    solid = rgba[..., 3] > SOLID
    present, lowest, highest = _lowest(solid)
    report = {'ground': kind, 'reflected_columns': int(present.sum()), 'strength': strength, 'length': length}
    if not present.any():
        return out, report
    lowest = _smoothed(lowest, present, SMOOTH)
    rows = rgba.shape[0]
    reach = numpy.where(present, ((lowest - highest + 1) * length).astype(int), 0)
    reach = numpy.minimum(reach, rows - 1 - lowest)
    reach = numpy.where(present, numpy.maximum(reach, 0), 0)
    longest = int(reach.max()) if reach.size else 0
    report['reach_px'] = longest
    for step in range(1, longest + 1):
        columns = numpy.nonzero(present & (reach >= step))[0]
        if not columns.size:
            break
        source = lowest[columns] - (step - 1); target = lowest[columns] + step
        usable = (source >= 0) & (target < rows)
        columns, source, target = columns[usable], source[usable], target[usable]
        coverage = numpy.clip((rgba[source, columns, 3] - .5) / (SOLID - .5), 0, 1)
        fade = strength * (1.0 - step / (reach[columns] + 1.0)) ** 1.6 * coverage
        fade = fade[:, None]
        out[target, columns] = out[target, columns] * (1 - fade) + rgb[source, columns] * fade
    return out, report


def _contact(solid):
    """Per column, the row where the product meets the floor in the picture: its lowest pixel, smoothed, and carried
    across columns with no product (a reflection may reach a little past the outline)."""
    present, lowest, highest = _lowest(solid)
    if not present.any():
        return None, None
    lowest = _smoothed(lowest, present, SMOOTH)
    columns = numpy.arange(solid.shape[1]); known = numpy.nonzero(present)[0]
    contact = numpy.interp(columns, known, lowest[known].astype(numpy.float32))
    tall = numpy.interp(columns, known, (lowest - highest + 1)[known].astype(numpy.float32))
    return contact, tall


def with_mirror(rgba, mirror, strength=MIRROR_STRENGTH, length=LENGTH, kind='white'):
    """A float RGBA render laid on its ground with a rendered reflection (``mirror``: float RGBA of the flipped copies,
    same camera) under it. The reflection shows only where the product does not, below where the product meets the
    floor, fading over ``length`` of the product's height in that column. Returns (RGB array, report)."""
    rgb = rgba[..., :3]; alpha = rgba[..., 3:4]
    out = rgb * alpha + ground(kind, rgba.shape[0], rgba.shape[1]) * (1.0 - alpha)
    solid = rgba[..., 3] > SOLID
    contact, tall = _contact(solid)
    report = {'ground': kind, 'method': 'mirror_pass', 'strength': strength, 'length': length}
    if contact is None:
        return out, report
    rows = numpy.arange(rgba.shape[0], dtype=numpy.float32)[:, None]
    below = rows - contact[None, :]
    # It fades out before the frame's lower edge, so a product standing low never carries its reflection off the picture.
    reach = numpy.maximum(numpy.minimum(tall * length, rgba.shape[0] - 1 - contact), 1.0)[None, :]
    # Just above the contact line the reflection meets the product's own base: let it start a few pixels up, under the product.
    fade = numpy.clip(1.0 - below / reach, 0, 1) ** 1.6 * (below > -3)
    # Hidden behind the product only: a shadow darkens what the floor scatters, not what it mirrors.
    clear = numpy.clip((SOLID - rgba[..., 3]) / (SOLID - .5), 0, 1)
    weight = strength * fade * mirror[..., 3] * clear
    weight = weight[..., None]
    out = out * (1 - weight) + mirror[..., :3] * weight
    report['reflected_pixels'] = int((weight[..., 0] > .02).sum())
    return out, report


def _read(path):
    import bpy
    source = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = source.size
        pixels = numpy.empty(width * height * 4, dtype=numpy.float32); source.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(source)
    return pixels.reshape(height, width, 4)[::-1], width, height     # Blender stores rows from the bottom


def mirror_pass(scene, target, floor_z=0.0, samples=None):
    """Render what the floor reflects: every visible mesh (as it is evaluated, with its modifiers and any skinning
    applied) and every light, flipped about the floor plane at ``floor_z``, with the originals and the floor hidden, on
    transparent film, written to ``target`` (PNG). The scene is left as it was. Returns True when the picture was written.

    The render takes at most a quarter of the main render's time limit; render hooks see ``studio_reflection_pass``
    on the scene and leave it alone."""
    import bpy
    from mathutils import Matrix
    flip = Matrix.Translation((0, 0, floor_z)) @ Matrix.Scale(-1, 4, (0, 0, 1)) @ Matrix.Translation((0, 0, -floor_z))
    hidden = []; made = []; meshes = []
    saved = (scene.render.filepath, scene.render.film_transparent, scene.cycles.samples, scene.render.image_settings.color_mode,
             scene.cycles.time_limit, scene.camera)
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for ob in list(scene.objects):
            if ob.hide_render or ob.type not in ('MESH', 'LIGHT'):
                continue
            if ob.name.startswith(('Studio floor', 'Glass negative fill')):
                hidden.append(ob); ob.hide_render = True
                continue
            if ob.type == 'MESH':
                # The mesh as it is drawn (armature skinning and other modifiers applied in its own space), then flipped:
                # flipping a copy that still carries an armature modifier would deform it in the wrong space.
                data = bpy.data.meshes.new_from_object(ob.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
                meshes.append(data)
                copy = bpy.data.objects.new(ob.name + ' (reflection)', data)
                for name in ('visible_camera', 'visible_shadow', 'visible_diffuse', 'visible_glossy', 'visible_transmission'):
                    if hasattr(ob, name):
                        setattr(copy, name, getattr(ob, name))
            else:
                copy = ob.copy()
            for collection in ob.users_collection:          # the same light-linking receiver sets as the original
                collection.objects.link(copy)
            copy.matrix_world = flip @ ob.matrix_world
            made.append(copy)
            hidden.append(ob); ob.hide_render = True
        if not any(copy.type == 'MESH' for copy in made):
            return False
        scene.render.film_transparent = True; scene.render.image_settings.color_mode = 'RGBA'
        if samples:
            scene.cycles.samples = samples
        if scene.cycles.time_limit:
            scene.cycles.time_limit = max(10.0, scene.cycles.time_limit / 4)
        scene.render.filepath = str(target)
        scene['studio_reflection_pass'] = True           # render hooks (blender/camera_entry.py, render_entry.py) leave this render alone
        bpy.ops.render.render(write_still=True)
        return True
    finally:
        if 'studio_reflection_pass' in scene:
            del scene['studio_reflection_pass']
        for copy in made:
            bpy.data.objects.remove(copy, do_unlink=True)
        for data in meshes:
            bpy.data.meshes.remove(data)
        for ob in hidden:
            ob.hide_render = False
        (scene.render.filepath, scene.render.film_transparent, scene.cycles.samples, scene.render.image_settings.color_mode,
         scene.cycles.time_limit, scene.camera) = saved


def finish_white(path, jpg=None, quality=95, kind='white', mirror=None):
    """Read a transparent-film PNG written by Blender, lay it on its ground with its reflection, and write it back as RGB.

    ``jpg``, when given, is written from the same pixels. Returns the report. The pixels of an 8-bit PNG are read and
    written as they are stored, so no colour transform is applied twice.
    """
    import bpy
    rgba, width, height = _read(path)
    if mirror is not None:
        reflected, mirror_width, mirror_height = _read(mirror)
        if (mirror_width, mirror_height) == (width, height):
            out, report = with_mirror(rgba, reflected, kind=kind)
        else:
            out, report = onto_white(rgba, kind=kind)
    else:
        out, report = onto_white(rgba, kind=kind)
    result = numpy.ones((height, width, 4), dtype=numpy.float32); result[..., :3] = numpy.clip(out, 0, 1)
    image = bpy.data.images.new('Studio reflection', width, height, alpha=False)
    try:
        image.colorspace_settings.name = 'sRGB'
        image.pixels.foreach_set(result[::-1].reshape(-1))
        for target, kind in ((path, 'PNG'), (jpg, 'JPEG')):
            if target is None:
                continue
            image.filepath_raw = str(target); image.file_format = kind
            if kind == 'JPEG':
                image.save(filepath=str(target), quality=quality)
            else:
                image.save(filepath=str(target))
    finally:
        bpy.data.images.remove(image)
    return {**report, 'size': [width, height]}

"""A clear reflection of the product on the floor it stands on.

Grey and dark studio floors are real objects: they are given a glossy coat, and Cycles
renders the reflection itself.

A white background is a shadow catcher, which carries shadows but not reflections, and a
glossy white floor would turn grey wherever it reflects the dim room. A light grey floor has
the same trouble: it reflects a room as dim as the product, so the reflection hardly shows.
So white and grey renders are made on transparent film over a shadow-catching floor and
finished here: laid on their ground (white, or the grey studio gradient) with their shadow,
and each column of the product mirrored down from where it meets the floor, fading with
distance. That is how a product on an acrylic sheet looks from a little above. Only numpy is needed, which
Blender ships with; the image is read and written with bpy.
"""
import numpy

STRENGTH = .5              # opacity of the reflection where it meets the product
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


def finish_white(path, jpg=None, quality=95, kind='white'):
    """Read a transparent-film PNG written by Blender, lay it on its ground with its reflection, and write it back as RGB.

    ``jpg``, when given, is written from the same pixels. Returns the report. The pixels of an 8-bit PNG are read and
    written as they are stored, so no colour transform is applied twice.
    """
    import bpy
    source = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = source.size
        pixels = numpy.empty(width * height * 4, dtype=numpy.float32); source.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(source)
    rgba = pixels.reshape(height, width, 4)[::-1]                 # Blender stores rows from the bottom
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

"""Geometry of a staged picture that needs no Blender: openings, standard stock sizes, dimension edges.

Kept apart from ``stage.py`` so the application and the tests can use it without ``bpy``.
"""
import math

# Nominal outside diameters of plastic and copper pipe, and common bar stock, in millimetres.
PIPE_SIZES = (6, 8, 10, 12, 15, 16, 20, 22, 25, 28, 32, 35, 40, 42, 50, 54, 63, 75, 90, 110, 125, 160)
BOARD_THICKNESSES = (3, 6, 9, 12, 15, 18, 22, 25, 38, 45)
MIN_OPENING = .04          # of the product's longest side: smaller gaps are vents and screw holes
ENCLOSED = .7              # share of directions that must be closed off: a jaw is, the outside of a corner is not


def largest_opening(occupied, nu, nv, directions=16, need=ENCLOSED):
    """The most enclosed empty disc of a silhouette: (column, row, radius in cells, enclosure 0..1), or None.

    ``occupied`` is the silhouette row by row. A cell counts when rays from it in ``directions``
    directions mostly end on the product: a hole is closed all round, a jaw on three sides, the
    outside of the product on hardly any.
    """
    if len(occupied) != nu * nv or not any(occupied):
        return None

    def solid(x, y):
        return 0 <= x < nu and 0 <= y < nv and occupied[y * nu + x]
    edge = [(x, y) for y in range(nv) for x in range(nu) if occupied[y * nu + x]
            and not (solid(x - 1, y) and solid(x + 1, y) and solid(x, y - 1) and solid(x, y + 1))]
    rays = [(math.cos(2 * math.pi * k / directions), math.sin(2 * math.pi * k / directions)) for k in range(directions)]
    best = None; found = []
    for y in range(nv):
        for x in range(nu):
            if occupied[y * nu + x]:
                continue
            blocked = 0
            for dx, dy in rays:
                px, py = x + .5, y + .5
                while True:
                    px += dx; py += dy
                    cx, cy = math.floor(px), math.floor(py)
                    if not (0 <= cx < nu and 0 <= cy < nv):
                        break
                    if occupied[cy * nu + cx]:
                        blocked += 1
                        break
            enclosure = blocked / directions
            if enclosure < need:
                continue
            radius = math.sqrt(min((ex - x) ** 2 + (ey - y) ** 2 for ex, ey in edge)) - .5
            score = radius * (enclosure - .5)
            if radius >= 1.5:
                found.append((x, y, radius, enclosure))
                if best is None or score > best[0]:
                    best = (score, x, y, radius, enclosure)
    if not best:
        return None
    # The score finds the opening; its size is the widest disc that fits there, which may sit a little nearer the mouth.
    _, bx, by, reach, _ = best
    return max((row for row in found if (row[0] - bx) ** 2 + (row[1] - by) ** 2 <= reach * reach), key=lambda row: (row[2], row[3]))


def stock_size(limit, sizes=PIPE_SIZES, fill=.92):
    """The largest standard size that fits ``limit`` with a little room; a plain fraction when none does."""
    fitting = [size for size in sizes if size <= limit * fill]
    return float(fitting[-1]) if fitting else round(max(.1, limit * .8), 1)


def model_size_mm(longest, suffix):
    """A guess at the real longest side from the file's own units. glTF is in metres; OBJ is usually millimetres."""
    longest = float(longest)
    if longest <= 0:
        return None
    if suffix.lower() == '.glb' or longest < 20:
        return round(longest * 1000, 1)       # metres
    return round(longest, 1)                    # millimetres


def dimension_text(mm, unit='mm'):
    """How a measured length is printed: whole millimetres, or inches to one decimal."""
    if unit == 'in':
        return ('%.1f' % (mm / 25.4)).rstrip('0').rstrip('.') + ' in'
    return '%d mm' % round(mm) if mm >= 10 else ('%.1f' % mm).rstrip('0').rstrip('.') + ' mm'

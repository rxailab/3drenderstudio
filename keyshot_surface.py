"""Compile a native KeyShot material graph to the PBR subset shared by both renderers.

This runs when building the local catalog. Referenced image files are resolved
through the catalog's allowlist. Measured spectra, specialized geometry shaders
and optical coatings use a color sampled from their shipped material preview.
"""
import math
import statistics
from pathlib import Path


def _clamp(value, low=0., high=1.):
    return max(low, min(high, float(value)))


def _rgb(value):
    if isinstance(value, list) and len(value) >= 3 and all(isinstance(x, (float, int)) for x in value[:3]):
        return [_clamp(x) for x in value[:3]]
    return None


def _mix(a, b, t=.5):
    return [x * (1-t) + y*t for x, y in zip(a, b)]


def _swatch(path):
    from PIL import Image
    with Image.open(path) as img:
        img = img.convert('RGB').resize((64, 64))
    # The 100 mm sample disc has a hole/lettering at the center and a studio
    # background outside the rim. Sample across the solid annulus only.
    samples = [img.getpixel((x,y)) for y in range(64) for x in range(64)
               if .34 < ((x-32)/27)**2 + ((y-31)/21)**2 < .65]
    srgb = [statistics.median(channel)/255 for channel in zip(*samples)]
    return [x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4 for x in srgb]


IMAGE_TYPES = {'texture2_rgba', 'texture2_rgb', 'triplanar', 'texture_normalmap_normal'}
TRANSPARENT = {'lux_glass', 'lux_glass_simple', 'lux_window_glass', 'lux_gem', 'lux_liquid',
               'lux_multilayer_thinfilm', 'lux_thinfilm'}
METALS = {'lux_metal', 'lux_metal_coated', 'lux_anisotropic'}
PAINT = {'metallic_paint', 'dupont_paint', 'lux_paint', 'bsdf_axf_carpaint'}
LIGHTS = {'lux_spotlight', 'lux_ies_light', 'lux_mesh_light', 'lux_emissive', 'lux_point_light'}
PATTERNS = {'lux_wood2':'wood','lux_wood':'wood','lux_weave2':'weave','lux_real_cloth':'weave',
            'lux_brushed2':'brush','lux_leather_tex':'leather','lux_marble':'marble',
            'lux_mesh':'grid','lux_granite':'grain','lux_fractal_noise':'grain',
            'lux_noise':'grain','lux_worley':'cells','lux_scratches_worley':'brush'}


def describe(graph, texture_lookup, preview_path):
    nodes = {n['id']:n for n in graph['nodes']}
    shared = {s['id']:s for s in graph.get('shared_records', [])}

    def fields(node):
        return node.get('fields', []) if node else []

    def value(node, key, default=None):
        for f in fields(node):
            if f.get('name') != key:
                continue
            if 'value' in f:
                return f['value']
            if f.get('tag') == '0xa1':
                return value(shared.get(f.get('id')), 'color', default)
        return default

    def ref(node, *keys):
        for f in fields(node):
            if f.get('name') in keys and f.get('node_id') in nodes:
                return nodes[f['node_id']]

    def walk(node, visited=None):
        visited = visited if visited is not None else set()
        if not node or node['id'] in visited:
            return
        visited.add(node['id'])
        yield node
        for f in fields(node):
            for ident in f.get('node_ids', [f.get('node_id')]):
                yield from walk(nodes.get(ident), visited)

    def rgb(node, *keys):
        for key in keys:
            color = _rgb(value(node, key))
            if color is not None:
                return color

    def texture(node):
        for n in walk(node):
            if n['type'] not in IMAGE_TYPES:
                continue
            name = value(n,'texture') or value(n,'textureX')
            if not isinstance(name, str):
                continue
            filename = name.replace('\\','/').rsplit('/',1)[-1].casefold()
            path = texture_lookup.get(filename)
            if path and Path(path).suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
                return str(path), n
        return None, None

    primary = next((b for b in graph['bindings'] if b['slot'] == 1), graph['bindings'][0])
    surface = nodes[primary['node_id']]
    bump = None
    # Composite entries store overlays before their base. Geometry/bump wrappers
    # are not surface shaders. Follow only the active base chain.
    seen = set()
    while surface['id'] not in seen:
        seen.add(surface['id'])
        kind = surface['type']
        if kind == 'lux_composite':
            layers = next((f['node_ids'] for f in fields(surface) if f.get('name')=='shaders' and 'node_ids' in f), [])
            child = nodes.get(layers[-1]) if layers else None
        elif kind in ('lux_bumpmap', 'lux_label', 'lux_fuzz'):
            if kind == 'lux_bumpmap':
                bump = ref(surface,'bumpmap')
            child = ref(surface,'shader','surface')
        else:
            break
        if not child:
            break
        surface = child

    kind = surface['type']
    category = graph['source'].split('/')[0]
    if not bump:
        bump = ref(surface, 'geometry_normal')
    spec = {'color':None, 'roughness':.45, 'metallic':0., 'transmission':0., 'coat':0.,
            'ior':1.5, 'emission_strength':0., 'alpha':1., 'sheen':0., 'anisotropy':0.,
            'subsurface':0., 'textures':{}}
    albedo = ('base_color','base','diffuse','color','transmission','warp_color','albedo','caps color','texture_color')
    selector = ref(surface,*albedo)
    spec['color'] = rgb(surface,*albedo)
    for channel,keys in [('base_color',albedo), ('roughness',('roughness','specular_roughness')),
                         ('metallic',('metallic','base_metalness')), ('alpha',('alpha','geometry_opacity'))]:
        path, image = texture(ref(surface,*keys))
        if path:
            spec['textures'][channel] = path
            if channel=='base_color' and value(image,'blend_color'):
                tint = rgb(image,'default_color')
                if tint is not None:
                    spec['texture_tint'] = tint
    path, bump_img = texture(bump)
    if not path:
        # KeyShot uses a secondary geometry binding for diamond plate relief.
        # A height input preserves its pattern without changing the target mesh.
        for binding in graph['bindings']:
            geometry = nodes[binding['node_id']]
            if geometry['type']=='lux_displace':
                path,bump_img = texture(ref(geometry,'displace'))
                if path:
                    break
    if path:
        is_normal = value(bump_img,'bump_mode') == 1 or value(bump_img,'normalX') or 'normal' in Path(path).stem.casefold()
        spec['textures']['normal' if is_normal else 'height'] = path
    for key, choices in {'roughness':('roughness','specular_roughness','warp_roughness','roughness_x'),
                         'metallic':('metallic','base_metalness','metal_coverage'),
                         'coat':('clearcoat','coat_weight'), 'alpha':('alpha','geometry_opacity'),
                         'transmission':('transmission_weight',),'sheen':('sheen','fuzz_weight'),
                         'subsurface':('subsurface_weight',),'anisotropy':('anisotropic','specular_roughness_anisotropy')}.items():
        for choice in choices:
            v = value(surface,choice)
            if isinstance(v,(int,float)):
                spec[key] = _clamp(v)
                break
    spec['ior'] = _clamp(value(surface,'ior') or value(surface,'specular_ior') or value(surface,'warp_ior') or 1.5, 1, 4)
    rough_input = ref(surface,'roughness','specular_roughness')
    if rough_input and rough_input['type']=='convert_rgba_to_float':
        limits = [float(value(rough_input,k,v)) for k,v in [('output_from',0),('output_to',1)]]
        spec['roughness'] = _clamp(sum(limits)/2)
        spec['roughness_range'] = [_clamp(v) for v in limits]
    if kind in METALS:
        spec['metallic'] = 1.
    if kind in PAINT:
        spec['coat'] = 1.
    if kind in ('dupont_paint','bsdf_axf_carpaint'):
        spec['metallic'] = .45
    if kind in TRANSPARENT:
        spec['transmission'] = 1.
    if kind == 'lux_cloudy_plastic':
        spec['transmission'] = 1 - .65*_clamp(value(surface,'cloudiness',.35))
    if kind in ('lux_advanced','lux_plastic'):
        transmitted = rgb(surface,'specular_transmission')
        if transmitted and max(transmitted)>0:
            spec['transmission'] = max(transmitted)
            spec['color'] = _mix(spec['color'] or transmitted, transmitted, spec['transmission'])
    if kind in ('lux_translucent','lux_translucent2','lux_scattering_medium'):
        spec['subsurface'] = .65
        spec['transmission'] = .25
    if kind in LIGHTS:
        power = value(surface,'intensity',value(surface,'power',value(surface,'multiplier',1)))
        spec['emission_strength'] = _clamp(math.log1p(max(0.,power)) if kind!='lux_emissive' else power, .05, 20)
    if category in ('RealCloth','Twinbru Fabrics','Fuzz') or kind in ('lux_real_cloth','lux_velvet'):
        spec['sheen'] = .65
    if kind == 'lux_diffuse':
        spec['roughness'] = 1.
    # The inverse spectral problem is underdetermined. Use the authored swatch
    # for coated/conductor tables, measured BRDFs and specialized optical shaders.
    swatch_color = (kind in ('dupont_paint','bsdf_axf_svbrdf','bsdf_axf_carpaint','lux_multilayer_thinfilm','lux_thinfilm',
                             'lux_cutaway','lux_displace','lux_flakes','lux_bubbles') or
                    category in ('Fuzz','Mold-Tech') or
                    (kind=='lux_metal_coated' and (value(surface,'coated') or value(surface,'preset',-1) >= 0)))
    tint_pattern = next((n for n in walk(selector or surface) if n['type'] in PATTERNS),None) if selector or kind=='lux_real_cloth' else None
    detail_pattern = tint_pattern or next((n for n in walk(bump) if n['type'] in PATTERNS),None)
    if detail_pattern:
        pattern = PATTERNS[detail_pattern['type']]
        color_keys = {'wood':('spring','fall'), 'weave':('warp_color','weft_color'),
                      'grid':('color','background')}.get(pattern,('color1','color2'))
        a = rgb(detail_pattern,color_keys[0],'color_1','inside_color')
        b = rgb(detail_pattern,color_keys[1],'color_2','outside_color')
        detail = {'type':pattern,'rotation':float(value(detail_pattern,'uv_angle',0))}
        if value(detail_pattern,'scale',0)>0:
            detail['size'] = value(detail_pattern,'scale')
        if detail_pattern['type']=='lux_real_cloth':
            data = value(detail_pattern,'pattern_data',{})
            if isinstance(data,dict) and data.get('pattern_mask'):
                detail['weave'] = {k:data[k] for k in ('pattern_mask','pattern_warps','pattern_wefts')}
            spec['sheen'] = .85 if value(detail_pattern,'flyaway_enabled') else .6
        if tint_pattern and a and b:
            detail['colors'] = [a,b]
            spec['color'] = _mix(a,b)
        spec['procedural'] = detail
        if pattern == 'brush':
            spec['anisotropy'] = .7
    if kind=='lux_real_cloth':
        spec['roughness'] = max(.35,spec['roughness'])
    if 'base_color' in spec['textures'] or (selector and not tint_pattern) or swatch_color or spec['color'] is None:
        spec['color'] = _swatch(preview_path)
    if nodes[primary['node_id']]['type']=='lux_composite' and 'base_color' not in spec['textures']:
        # An overlay may cover most of the visible area (painted hexagon
        # tiles over grey grout). Its coverage isn't a constant mix weight.
        spec['color'] = _swatch(preview_path)
        if 'procedural' in spec:
            spec['procedural'].pop('colors',None)
    if kind=='lux_toon':
        spec['procedural'] = {'type':'toon','colors':[rgb(surface,'contour color') or [0,0,0], spec['color']]}
    if kind=='lux_metal_coated' and value(surface,'coated'):
        spec['coat'] = .45
    return spec

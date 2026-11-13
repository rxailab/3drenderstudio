"""Usage scenes: put a product where it is used, with things that belong beside it.

A tool cabinet gets a workshop floor, wrenches on its top, a drawer pulled out
with tools in it and a toolbox next to it. A pump bottle gets a counter and a
plant. The work is split between agents, each with one job:

    strategist who buys this product, which picture answers their question
    director   what is this product, where is it used, what would lie around it
    scout      which ready-made models from the catalogue stand in for each wish
    dresser    where each one can physically rest (Blender, by measurement)
    reviewer   keeps what was placed, records what was left out and why

The strategist reads the audience reference pack (scene_audience.py) and
proposes a buyer segment, a reference to borrow the idea from and the picture
to make; the user may change any of it. A picture that needs a person or a
real place is then made by the image model from renders of the product
(scene_imagegen.py); every other picture is built from 3D props as below.

The strategist, the director and the scout use the configured language model
when there is one and fixed recipes when there is not. The dresser and
reviewer are plain code. Models come from Poly Haven (CC0); see scene_props.py.

The result is stored in the plan as a new shot plus plan['scene_set'] (props)
or an image output (generated), with plan['scene_brief'] recording who it is
for, in one undoable version.
"""
import copy
import json
import math
import random
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

import engine
import planner
import scene_audience
import scene_props
import scene_set
from studio_store import DATA, LOCK, ROOT, all_records, atomic_json, get, put

KIND = 'scene_task'
ACTIVE = {'queued', 'running'}
RUNNING_TASKS = set()
SHOT_ID = 'scene'
LAYOUT_TIMEOUT = 240
MAX_HINT = 300
GENERATED = 'generated'        # quality of an output that the image model made
# The segment a recipe's products are usually sold to, when their name says nothing more.
RECIPE_SEGMENTS = {'tool_storage': 'site_team', 'garden': 'home_garden'}
UNITS = (('mm', 1), ('cm', 10), ('inch', 25.4), ('m', 1000))

# What lies around a product, when no model is there to decide. Each prop is a
# short list of catalogue searches (the first that exists is used), where it
# goes and how it rests. Sizes are the plausible largest dimension in mm.
RECIPES = [
    {'id': 'tool_storage', 'label': '工具柜 / 工具箱', 'setting': 'workshop', 'size_mm': (300, 1800), 'open': ('抽屉', 'drawer'),
     # A unit of drawers on casters is a workshop trolley even when nothing is called a tool.
     'part_cues': ((('抽屉', 'drawer'), ('轮子', '脚轮', 'caster', 'wheel')),),
     'words': ('工具柜', '工具箱', '工具车', '工具', '抽屉柜', '工作台', 'toolbox', 'tool box', 'tool cabinet', 'tool chest', 'tool cart', 'workbench'),
     'props': [(('adjustable wrench', 'pipe wrench'), 'on_top', 'flat'), (('pliers',), 'on_top', 'flat'),
               (('measuring tape',), 'on_top', 'upright'), (('lubricant spray', 'oil can'), 'on_top', 'upright'),
               (('screwdriver',), 'inside', 'flat'), (('combination wrench',), 'inside', 'flat'),
               (('ratchet wrench', 'tongue groove pliers'), 'inside', 'flat'),
               (('cross pein hammer', 'hammer'), 'beside', 'flat'), (('metal toolbox',), 'beside', 'upright')]},
    {'id': 'personal_care', 'label': '洗护 / 美妆', 'setting': 'bathroom', 'size_mm': (50, 450),
     'words': ('洗手液', '沐浴', '洗发', '护肤', '乳液', '按压瓶', '香水', '化妆', '瓶', 'lotion', 'shampoo', 'soap', 'cosmetic', 'skincare',
               'perfume', 'pump', 'bottle'),
     'props': [(('potted plant',), 'behind', 'upright'), (('ceramic vase',), 'behind', 'upright'),
               (('wooden bowl',), 'beside', 'upright'), (('wicker basket',), 'behind', 'upright')]},
    {'id': 'tableware', 'label': '餐厨用品', 'setting': 'kitchen', 'size_mm': (40, 700),
     'words': ('杯', '碗', '盘', '托盘', '餐具', '水壶', '锅', '厨房', 'tray', 'cup', 'glass', 'tumbler', 'mug', 'bowl', 'plate', 'kettle',
               'pot', 'pan', 'kitchen'),
     'props': [(('jug',), 'behind', 'upright'), (('apple', 'fruit'), 'beside', 'upright'),
               (('wooden cutting board',), 'behind', 'flat'), (('wooden bowl',), 'beside', 'upright'),
               (('potted plant',), 'behind', 'upright')]},
    {'id': 'bag', 'label': '箱包', 'setting': 'living', 'size_mm': (120, 800),
     'words': ('手提袋', '背包', '钱包', '箱包', '包', 'bag', 'tote', 'backpack', 'wallet', 'purse', 'handbag'),
     'props': [(('book',), 'beside', 'upright'), (('potted plant',), 'behind', 'upright'), (('camera',), 'beside', 'upright'),
               (('ceramic vase',), 'behind', 'upright')]},
    {'id': 'garden', 'label': '园艺 / 户外', 'setting': 'garden', 'size_mm': (100, 2000),
     'words': ('花盆', '园艺', '浇水', '户外', '庭院', 'garden', 'planter', 'watering', 'outdoor', 'patio'),
     'props': [(('watering can',), 'beside', 'upright'), (('potted plant',), 'beside', 'upright'), (('trowel',), 'beside', 'flat')]},
    {'id': 'electronics', 'label': '数码 / 办公', 'setting': 'office', 'size_mm': (30, 900),
     'words': ('耳机', '音箱', '键盘', '鼠标', '显示器', '相机', '手机', '台灯', '充电', 'speaker', 'keyboard', 'mouse', 'monitor', 'camera',
               'phone', 'headphone', 'lamp', 'charger', 'laptop'),
     'props': [(('book',), 'beside', 'upright'), (('potted plant',), 'behind', 'upright'), (('binder notebook',), 'beside', 'flat')]},
    {'id': 'furniture', 'label': '家具', 'setting': 'living', 'size_mm': (250, 2600), 'open': ('抽屉', 'drawer'),
     'part_cues': ((('抽屉', 'drawer', '柜门', 'door'),),),
     'words': ('椅', '凳', '桌', '沙发', '柜', '架', '床', 'chair', 'stool', 'table', 'sofa', 'shelf', 'cabinet', 'desk', 'bed', 'furniture'),
     'props': [(('potted plant',), 'behind', 'upright'), (('book',), 'on_top', 'upright'), (('ceramic vase',), 'on_top', 'upright'),
               (('wicker basket',), 'beside', 'upright')]},
]
GENERIC = {'id': 'generic', 'label': '通用陈列', 'setting': 'studio', 'size_mm': (60, 1500), 'words': (),
           'props': [(('potted plant',), 'behind', 'upright'), (('book',), 'beside', 'upright'), (('ceramic vase',), 'beside', 'upright')]}
# Floor and wall per setting: preferred catalogue textures, then a search, then a plain colour.
SURFACES = {
    'workshop': {'floor': ('scuffed_cement', 'smooth_concrete_floor', 'concrete floor'), 'wall': ('concrete_wall_008', 'plaster_grey_04', 'concrete wall'),
                 'floor_color': [.55, .53, .5], 'wall_color': [.6, .6, .58]},
    # At home the wall is plain paint: a clean backdrop the product stands out against.
    'kitchen': {'floor': ('oak_veneer_01', 'wood_table', 'wood table'), 'wall': (), 'floor_color': [.62, .5, .38], 'wall_color': [.9, .89, .86]},
    'bathroom': {'floor': ('marble_01', 'floor_tiles_08', 'tiles'), 'wall': (), 'floor_color': [.82, .82, .8], 'wall_color': [.9, .91, .9]},
    'living': {'floor': ('laminate_floor_02', 'wood_floor', 'wood floor'), 'wall': (), 'floor_color': [.6, .48, .36], 'wall_color': [.88, .86, .82]},
    'office': {'floor': ('oak_veneer_01', 'plywood', 'wood table'), 'wall': (), 'floor_color': [.62, .52, .4], 'wall_color': [.88, .88, .86]},
    'garden': {'floor': ('patio_tiles', 'large_square_pattern_01', 'tiles'), 'wall': ('brown_planks_05', 'brick_wall_003', 'planks wall'),
               'floor_color': [.55, .52, .47], 'wall_color': [.5, .42, .34]},
    'studio': {'floor': ('white_plaster_02', 'plaster'), 'wall': (), 'floor_color': [.8, .8, .78], 'wall_color': [.86, .86, .84]},
}
# How large a prop may be next to the product, by where it goes: (smallest, ideal, largest) as shares of the product's size.
PROP_SIZE = {'on_top': (.05, .3, .75), 'inside': (.05, .3, .75), 'beside': (.08, .45, .9), 'behind': (.45, 1.1, 1.8)}
EXCLUDED = ('weapons', 'firearms', 'buildings', 'structures', 'trees', 'ground cover', 'rocks', 'ships', 'creature')

DIRECTOR_PROMPT = '''You direct product photography. You are shown one product (a render of its 3D model, with facts about it) and decide the usage scene it should be photographed in: where this product is really used, and which everyday things would naturally lie on it, in it and beside it there.
Return data only:
- product_type: what the product is, a few words in the user's language.
- setting: the closest of the supported settings.
- max_dimension_mm: the real size of the product's largest dimension, your best estimate from what it is. model_dimensions are in unknown units; use them only for proportions.
- props: 3 to 9 things. concept is a short plain English noun phrase for ONE thing ("adjustable wrench", "potted plant"); prefer things from available_models, which lists every ready-made model that can actually be used, and do not ask for the product itself or another product of its kind. zone says where it goes: on_top (resting on the product's flat top), inside (in a drawer or compartment that will be opened to show it), beside (on the ground next to it), behind (taller things set back to give depth). pose is flat for things that lie down (hand tools, books lying), upright for things that stand, auto if unsure.
- open_compartment: true only if the product visibly has drawers or a compartment and showing its contents helps.
- summary: one sentence in the user's language describing the scene.
Choose things a real user of this product would have at hand; a believable scene beats a full one. Small products sit on a table or counter: their props go beside and behind, never on_top. Never use zone inside unless open_compartment is true. audience, when present, says who the picture is for, the question they need answered and what to emphasise; planned_picture is the picture already agreed for them. Choose the setting and the props that serve it, as far as loose objects around the product can: this kind of scene has no people in it, so summary describes only the place and the objects that will be there. hint, when present, is what the user asked for in their own words and takes priority.'''

STRATEGIST_PROMPT = '''You plan product imagery for online shops. You are shown one product (a render of its 3D model, with facts about it) and a library of buyer segments. Each segment says who buys, the question they need answered before buying, the kinds of image that answer it and what to emphasise. Under each are numbered references: published images described in words (what the picture shows, and what it does well). They are study notes on composition, not content to copy.
Decide which segment this product's usage scene should address and plan one picture for it. Return data only:
- segment_id: the segment whose buyers most plausibly shop for this product. An empty string when none would: the library covers tools, workshop, jobsite, maintenance and garden equipment, so a cosmetics bottle or a handbag fits none. When required_segment is given, use it.
- reason: one or two sentences in the user's language saying why, from what the product is.
- reference_id: the reference of that segment whose idea transfers best to this product (how the product is shown, what else is in the frame), or an empty string. When required_reference is given, use it and build the picture on it.
- product: what the product is, two to five plain English words.
- max_dimension_mm: the real size of the product's largest dimension, your best estimate from what it is. model_dimensions are in unknown units; use them only for proportions.
- summary: one sentence in the user's language describing the planned picture.
- people: true when the picture needs a person, or a person's hands, to make its point.
- complex: true when it needs a place that a floor, one back wall and a few loose objects around the product cannot give: a jobsite, a vehicle, a lawn, a furnished room, the outdoors.
- scene: the picture in plain English, 40 to 110 words, written for an image generator: the place and its light, what the person (if any) is doing with the product, where the product stands in the frame and how much of the frame it takes. Call the product by what it is ("the tool cabinet") and do not describe its design; its exact look is supplied separately as renders. Never name a brand, a model number or a publisher, and ask for no text in the picture.
- setting: the closest of supported_settings, used when the scene is built from 3D props instead.
- azimuth, elevation: the camera angle on the product that suits the picture, in degrees, in the convention of hero_view: azimuth 0 faces the product's front and elevation is the height above level. Stay within 70 degrees of the front.
Plan a picture a shop could really use: the product is the subject, complete and prominent. hint, when present, is what the user asked for in their own words and takes priority over the references.'''

SCOUT_PROMPT = '''You collect ready-made 3D models for a product photography set. For each wish you are given the candidates that the catalogue search found, with their names, tags and real dimensions in millimetres, and the product's real size.
Return one choice per wish, in the same order, with its index. asset_id is the candidate that best stands in for the wish at a believable size next to this product, or an empty string when none of the candidates is that thing (a different object of the same category is not good enough, and nothing is better than something odd). Prefer the plain, clean version of a thing over an ornate or damaged one. Return data only.'''


def _object(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties if required is None else required),
            'additionalProperties': False}


def director_schema():
    prop = _object({'concept': {'type': 'string', 'minLength': 2, 'maxLength': 60},
                    'zone': {'type': 'string', 'enum': list(scene_set.ZONES)},
                    'pose': {'type': 'string', 'enum': list(scene_set.POSES)}})
    return _object({'product_type': {'type': 'string', 'minLength': 1, 'maxLength': 80},
                    'setting': {'type': 'string', 'enum': list(scene_set.SETTINGS)},
                    'max_dimension_mm': {'type': 'number', 'minimum': 10, 'maximum': 6000},
                    'props': {'type': 'array', 'items': prop, 'minItems': 1, 'maxItems': 10},
                    'open_compartment': {'type': 'boolean'},
                    'summary': {'type': 'string', 'minLength': 1, 'maxLength': 300}})


def strategist_schema(segments):
    text = lambda low, high: {'type': 'string', 'minLength': low, 'maxLength': high}
    return _object({'segment_id': {'type': 'string', 'enum': [item['id'] for item in segments] + ['']},
                    'reason': text(1, 300), 'reference_id': {'type': 'string', 'maxLength': 5, 'pattern': r'(?:[0-9]{2}-[0-9]{2})?'},
                    'product': text(1, 60), 'max_dimension_mm': {'type': 'number', 'minimum': 10, 'maximum': 6000},
                    'summary': text(1, 300), 'people': {'type': 'boolean'}, 'complex': {'type': 'boolean'}, 'scene': text(20, 1200),
                    'setting': {'type': 'string', 'enum': list(scene_set.SETTINGS)},
                    'azimuth': {'type': 'number', 'minimum': -180, 'maximum': 180},
                    'elevation': {'type': 'number', 'minimum': -10, 'maximum': 90}})


def scout_schema(count):
    choice = _object({'index': {'type': 'integer', 'minimum': 0, 'maximum': max(0, count - 1)},
                      'asset_id': {'type': 'string', 'maxLength': 100}})
    return _object({'choices': {'type': 'array', 'items': choice, 'minItems': count, 'maxItems': count}})


def _ask(config, system, facts, schema, image_path=None):
    import ai_client as ai
    body = ai._payload(config, system, facts, schema, image_path=image_path, label_images=bool(image_path), max_tokens=3000)
    output = ai._response(ai._post(config, body), config, ai.TOOL_NAME)
    ai._check_schema(output, schema)
    return output


def _config(image):
    import ai_config
    config = ai_config.get_vision_config() if image else ai_config.get_config()
    return config if all(config.get(key) for key in ('enabled', 'api_key', 'model')) else None


def _text_of(project):
    metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
    parts = [project.get('name', ''), metadata.get('product_category', ''), metadata.get('material_note', '')]
    parts += [item.get('name', '') for item in (project.get('analysis') or {}).get('objects', [])[:400] if isinstance(item, dict)]
    return ' '.join(str(part) for part in parts if part).casefold()


def recipe_for(project, hint=''):
    """The recipe whose words describe this project best; the generic one otherwise."""
    metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
    strong = ' '.join(str(value) for value in (hint, project.get('name', ''), metadata.get('product_category', ''))).casefold()
    weak = _text_of(project)
    best = None
    for recipe in RECIPES:
        # A longer matching word says more: "tool cabinet" outweighs "cabinet", 工具柜 outweighs 柜.
        score = sum(2 + min(len(word), 12) / 4 for word in recipe['words'] if word in strong) + sum(1 for word in recipe['words'] if word in weak)
        for groups in recipe.get('part_cues', ()):
            if all(any(word in weak for word in group) for group in groups):
                score += len(groups)
        if score and (best is None or score > best[0]):
            best = (score, recipe)
    return best[1] if best else GENERIC


def real_size(project, size_range, estimate=None):
    """(largest dimension in mm, basis). The user's own figures always win."""
    plan = project['plan']
    if plan.get('dimensions_mm'):
        return float(max(plan['dimensions_mm'])), 'user'
    if estimate:
        return float(estimate), 'ai'
    analysis = project.get('analysis') or {}
    measured = analysis.get('dimensions', {}).get('value') or analysis.get('features', {}).get('dimensions')
    low, high = size_range
    if isinstance(measured, list) and measured and all(type(v) in (int, float) and v > 0 for v in measured):
        largest = max(measured)
        # The model's own units are unknown: take the unit that makes it a plausible size for what it is.
        fits = [largest * factor for _, factor in UNITS if low <= largest * factor <= high]
        if fits:
            centre = (low * high) ** .5
            return float(min(fits, key=lambda value: abs(value / centre - 1))), 'model_units'
    return float((low * high) ** .5), 'category'


def _hero(plan):
    return next((shot for shot in plan['shots'] if shot['id'] == 'hero'), plan['shots'][0])


def _view(plan, azimuth=None, elevation=None):
    """The angle to show the product from: the proposed one, kept to its front, else close to the main image."""
    hero = _hero(plan)
    if type(azimuth) not in (int, float):
        azimuth = hero.get('azimuth', 35) if abs(hero.get('azimuth', 35)) <= 70 else 35
    if type(elevation) not in (int, float):
        elevation = hero.get('elevation', 20) + 4
    return round(max(-70.0, min(70.0, float(azimuth))), 1), round(max(8.0, min(55.0, float(elevation))), 1)


def _line(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def with_method(proposal, requested=None):
    """How the proposed picture gets made. People or a real place need the image model; the rest is built in 3D."""
    import scene_imagegen
    available = scene_imagegen.config() is not None
    wanted = 'generate' if proposal.get('people') or proposal.get('complex') else 'render'
    note = None
    if requested == 'generate' and not available:
        raise ValueError('图像模型未配置或已关闭，无法用 AI 生成场景图；可改用「3D 道具场景」。')
    if requested in scene_audience.METHODS:
        method = requested
    elif wanted == 'generate' and not available:
        method, note = 'render', '这个画面需要人物或真实环境，适合由图像模型生成；图像模型未配置，已改为 3D 道具场景（不含人物）。'
    else:
        method = wanted
    return {**proposal, 'method': method, 'method_note': note, 'image_available': available}


def propose(project, hint='', audience=None, reference=None, image_path=None):
    """Strategist: who the scene is for and what it shows.

    ``audience`` and ``reference`` are the user's own choices, when they made
    any. Rules answer when no model does, so this returns a proposal whenever
    its arguments are valid.
    """
    segments = scene_audience.library()
    required = audience if audience not in (None, '', 'auto') else None
    if required and not scene_audience.segment(required):
        raise ValueError('所选人群不在参考库中。')
    if reference and not (required and scene_audience.reference(reference, required)):
        raise ValueError('所选参考不属于这个人群。')
    plan = project['plan']; hero = _hero(plan); recipe = recipe_for(project, hint)
    base = {'id': uuid.uuid4().hex, 'version': project['version'], 'hint': hint, 'audience': required or 'auto',
            'reference': reference or None, 'at': time.time()}
    config = _config(image_path) if segments else None
    if config:
        analysis = project.get('analysis') or {}
        metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
        facts = {'product_name': str(project.get('name', ''))[:200], 'product_category': str(metadata.get('product_category', ''))[:200],
                 'material_note': str(metadata.get('material_note', ''))[:500],
                 'part_names': [str(item.get('name', ''))[:80] for item in analysis.get('objects', [])[:60] if isinstance(item, dict)],
                 'model_dimensions': analysis.get('features', {}).get('dimensions'), 'real_dimensions_mm': plan.get('dimensions_mm'),
                 'hero_view': {'azimuth': hero.get('azimuth'), 'elevation': hero.get('elevation')},
                 'supported_settings': list(scene_set.SETTINGS),
                 'segments': [{'id': item['id'], 'buyer': item['label'], 'buying_question': item['question'],
                               'images_that_answer_it': item['priority'], 'emphasis': item['focus'],
                               'references': [{'id': row['id'], 'shows': row['title'] + '：' + row['locator'], 'does_well': row['note']}
                                              for row in item['references']]} for item in segments]}
        if hint: facts['hint'] = hint
        if required: facts['required_segment'] = required
        if reference: facts['required_reference'] = reference
        try:
            answer = _ask(config, STRATEGIST_PROMPT, facts, strategist_schema(segments), image_path)
            segment_id = required or answer['segment_id'] or None
            row = scene_audience.reference(reference or answer['reference_id'], segment_id) if segment_id else None
            azimuth, elevation = _view(plan, answer['azimuth'], answer['elevation'])
            return with_method({**base, 'segment_id': segment_id, 'reference_id': row['id'] if row else None, 'source': 'ai',
                                'reason': _line(answer['reason'], 300), 'product': _line(answer['product'], 60),
                                'max_dimension_mm': answer['max_dimension_mm'], 'summary': _line(answer['summary'], 300),
                                'people': answer['people'], 'complex': answer['complex'],
                                'scene': scene_audience.scrub(_line(answer['scene'], 1200)), 'setting': answer['setting'],
                                'azimuth': azimuth, 'elevation': elevation})
        except Exception:      # no answer is not a reason to have no proposal
            pass
    metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
    strong = ' '.join(str(value) for value in (hint, project.get('name', ''), metadata.get('product_category', '')))
    segment_id, row, reason = scene_audience.propose_rules(_text_of(project), strong, required, RECIPE_SEGMENTS.get(recipe['id']))
    if reference:
        row = scene_audience.reference(reference, segment_id)
    chosen = scene_audience.segment(segment_id) if segment_id else None
    azimuth, elevation = _view(plan)
    return with_method({**base, 'segment_id': segment_id, 'reference_id': row['id'] if row else None, 'source': 'rules', 'reason': reason,
                        'product': _line(project.get('name') or recipe['label'], 60), 'max_dimension_mm': None,
                        'summary': '面向「%s」：%s。' % (chosen['label'], chosen['priority']) if chosen else '%s的通用使用场景。' % recipe['label'],
                        'people': False, 'complex': False, 'scene': chosen['scene'] if chosen else '', 'setting': recipe['setting'],
                        'azimuth': azimuth, 'elevation': elevation})


def _brief(proposal, method):
    """What the plan remembers of a proposal: who the scene is for and how it was made."""
    return {'version': 1, 'shot_id': SHOT_ID, 'segment_id': proposal.get('segment_id'), 'reference_id': proposal.get('reference_id'),
            'source': 'user' if proposal.get('audience') not in (None, 'auto') else proposal.get('source', 'rules'), 'method': method,
            'reason': _line(proposal.get('reason'), scene_audience.MAX_TEXT), 'summary': _line(proposal.get('summary'), scene_audience.MAX_TEXT),
            'people': bool(proposal.get('people')), 'complex': bool(proposal.get('complex'))}


def direct(project, hint, image_path, models, proposal=None):
    """Director: the scene brief. Returns (brief, source)."""
    recipe = recipe_for(project, hint)
    config = _config(image_path)
    if config:
        analysis = project.get('analysis') or {}
        metadata = project.get('metadata', {}) if isinstance(project.get('metadata'), dict) else {}
        facts = {'product_name': str(project.get('name', ''))[:200], 'product_category': str(metadata.get('product_category', ''))[:200],
                 'material_note': str(metadata.get('material_note', ''))[:500],
                 'part_names': [str(item.get('name', ''))[:80] for item in analysis.get('objects', [])[:60] if isinstance(item, dict)],
                 'model_dimensions': analysis.get('features', {}).get('dimensions'),
                 'real_dimensions_mm': project['plan'].get('dimensions_mm'),
                 'supported_settings': list(scene_set.SETTINGS), 'zones': list(scene_set.ZONES),
                 'available_models': sorted(str(asset.get('name', key))[:60] for key, asset in models.items()
                                            if not set(asset.get('categories', [])) & set(EXCLUDED))}
        if hint:
            facts['hint'] = hint
        audience = scene_audience.brief_text(proposal.get('segment_id'), proposal.get('reference_id')) if proposal else None
        if audience:
            facts['audience'] = audience
            if proposal.get('source') == 'ai' and proposal.get('summary'):
                facts['planned_picture'] = proposal['summary']
        try:
            answer = _ask(config, DIRECTOR_PROMPT, facts, director_schema(), image_path)
            props = [(tuple([item['concept']]), item['zone'], item['pose']) for item in answer['props']]
            if not answer['open_compartment']:
                props = [(concepts, 'on_top' if zone == 'inside' else zone, pose) for concepts, zone, pose in props]
            return {'recipe': 'ai', 'label': answer['product_type'], 'setting': answer['setting'],
                    'size_mm': recipe['size_mm'], 'estimate_mm': answer['max_dimension_mm'], 'props': props,
                    'open': ('抽屉', 'drawer', 'tray', '托盘') if answer['open_compartment'] else (),
                    'summary': answer['summary']}, 'ai'
        except Exception:      # no answer is not a reason to have no scene
            pass
    return {'recipe': recipe['id'], 'label': recipe['label'], 'setting': recipe['setting'], 'size_mm': recipe['size_mm'],
            'estimate_mm': None, 'props': list(recipe['props']), 'open': tuple(recipe.get('open', ())), 'summary': None}, 'rules'


def scout(brief, product_mm, models, image_path=None, use_model=True):
    """Scout: one catalogue model per wish, sized believably. Returns (choices, skipped)."""
    wishes = []
    for concepts, zone, pose in brief['props']:
        smallest, ideal, largest = (product_mm * share for share in PROP_SIZE[zone])
        candidates = []
        for concept in concepts:
            found = scene_props.search(concept, limit=6, exclude=EXCLUDED, largest_mm=largest, smallest_mm=smallest, assets=models)
            found = [row for row in found if row['score'] >= 2.5]
            if found:
                # Among equally good matches, the one whose real size suits this product best.
                top = found[0]['score']
                near = [row for row in found if row['score'] >= top - .5]
                near.sort(key=lambda row: abs(math.log(max(row['dimensions_mm'] or [ideal]) / ideal)))
                candidates = near + [row for row in found if row not in near]
                break
        wishes.append({'concept': concepts[0], 'zone': zone, 'pose': pose, 'candidates': candidates})
    picked = {index: wish['candidates'][0]['asset_id'] for index, wish in enumerate(wishes) if wish['candidates']}
    config = _config(image_path) if use_model else None
    asked = [index for index, wish in enumerate(wishes) if len(wish['candidates']) > 1]
    if config and asked:
        facts = {'product_largest_dimension_mm': round(product_mm), 'setting': brief['setting'],
                 'wishes': [{'index': position, 'wish': wishes[index]['concept'], 'zone': wishes[index]['zone'],
                             'candidates': [{key: row[key] for key in ('asset_id', 'name', 'tags', 'categories', 'dimensions_mm')}
                                            for row in wishes[index]['candidates']]} for position, index in enumerate(asked)]}
        try:
            answer = _ask(config, SCOUT_PROMPT, facts, scout_schema(len(asked)))
            for choice in answer['choices']:
                index = asked[choice['index']]
                allowed = {row['asset_id'] for row in wishes[index]['candidates']}
                if choice['asset_id'] in allowed: picked[index] = choice['asset_id']
                elif choice['asset_id'] == '': picked.pop(index, None)
        except Exception:
            pass
    choices = []; skipped = []; used = set()
    for index, wish in enumerate(wishes):
        asset_id = picked.get(index)
        if not asset_id:
            skipped.append({'concept': wish['concept'], 'reason': 'not_in_catalogue'}); continue
        if asset_id in used:
            skipped.append({'concept': wish['concept'], 'reason': 'duplicate', 'asset_id': asset_id}); continue
        used.add(asset_id)
        choices.append({'concept': wish['concept'], 'zone': wish['zone'], 'pose': wish['pose'], 'asset_id': asset_id})
    return choices[:scene_set.MAX_PROPS], skipped


def _surface(kind, setting, folder, textures, cancelled):
    """A floor or wall for the setting: a preferred texture, a found one, or just its colour."""
    preset = SURFACES[setting]; color = preset[kind + '_color']
    for name in preset[kind]:
        asset_id = name if name in textures else None
        if asset_id is None:
            found = scene_props.search(name, kind='textures', limit=1, require=('wall',) if kind == 'wall' else (), assets=textures)
            asset_id = found[0]['asset_id'] if found else None
        if asset_id:
            try:
                record = scene_props.fetch_surface(asset_id, folder, '2k', cancelled)
            except scene_props.PropError:
                continue
            return {'asset_id': record['asset_id'], 'name': record['name'], 'textures': record['textures'], 'tile_m': record['tile_m'],
                    'color': color, 'source_url': record['source_url'], 'license': record['license']}
    return {'color': color}


def _layout(project, plan, shot, props, units, seed, keywords, folder, elevation_open, wall_distance_m=.6):
    executable = engine.blender_path(); script = ROOT / 'blender' / 'scene_layout.py'
    if not executable or not script.is_file():
        raise ValueError('场景布置组件未就绪，请确认 Blender 可用并已更新 blender/scene_layout.py。')
    folder.mkdir(parents=True, exist_ok=True)
    request = {'output_dir': str(folder), 'model_path': project['model_path'], 'plan': plan, 'shot': shot,
               'props': [{'id': prop['id'], 'file': prop['file'], 'zone': prop['zone'], 'pose': prop['pose']} for prop in props],
               'units_per_meter': units, 'seed': seed, 'open_keywords': list(keywords),
               'elevation_open': elevation_open, 'wall_distance_m': wall_distance_m}
    atomic_json(folder / 'request.json', request)
    (folder / 'result.json').unlink(missing_ok=True)
    command = [executable, '-b', '--factory-startup', '--disable-autoexec', '-t', '4', '--python-exit-code', '1',
               '--python', str(script), '--', str(folder / 'request.json')]
    try:
        with (folder / 'layout.log').open('w', encoding='utf-8') as log:
            ran = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=LAYOUT_TIMEOUT,
                                 cwd=str(ROOT), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError('场景布置未完成，请重试。') from exc
    try:
        result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        result = None
    if ran.returncode or not isinstance(result, dict) or result.get('success') is not True:
        raise ValueError('场景布置失败，当前方案未修改。')
    return result


def _update(task_id, **fields):
    with LOCK:
        task = get(KIND, task_id)
        if task['status'] == 'cancelled':
            raise InterruptedError()
        task.update(fields); put(KIND, task)
        return task


def _source_image(project):
    """A render that shows the agents what the product is: of the current plan when there is one, else its last main image.

    A picture the image model made is never used: it shows one imagined scene, and
    would lead the next proposal back to the same audience.
    """
    rows = [o for o in project.get('outputs', []) if o.get('quality') not in ('thumbnail', GENERATED)]
    current = [o for o in rows if o.get('version') == project['version']]
    current.sort(key=lambda o: (o.get('shot_id') != 'hero', -(o.get('created_at') or 0)))
    earlier = sorted((o for o in rows if o.get('version') != project['version'] and o.get('shot_id') == 'hero'),
                     key=lambda o: (-(o.get('version') or 0), -(o.get('created_at') or 0)))
    for output in current + earlier:
        url = output.get('png_url')
        if isinstance(url, str) and url.startswith('/files/'):
            path = (DATA / url[7:]).resolve()
            if (DATA / project['id']).resolve() in path.parents and path.is_file():
                return path
    return None


def build(project, hint='', seed=None, cancelled=None, progress=None, folder=None, proposal=None):
    """A scene of 3D props, everything short of saving: returns (plan with the scene, record for the task)."""
    say = progress or (lambda stage: None)
    stop = cancelled or (lambda: False)
    plan = copy.deepcopy(project['plan'])
    seed = random.randrange(1, 10 ** 6) if seed is None else int(seed)
    say('识别产品与使用场景')
    models = scene_props.catalogue('models')
    image = _source_image(project)
    brief, source = direct(project, hint, image, models, proposal)
    size_mm, basis = real_size(project, brief['size_mm'], brief.get('estimate_mm'))
    say('从 Poly Haven 搜集道具模型')
    choices, skipped = scout(brief, size_mm, models, image, use_model=source == 'ai')
    if not choices:
        raise ValueError('没有找到适合这个产品的现成道具模型，当前方案未修改。')
    props_folder = DATA / project['id'] / 'props'
    cache = DATA / 'props_cache'
    props = []
    for choice in choices:
        if stop(): raise InterruptedError()
        try:
            record = scene_props.adopt(scene_props.fetch_model(choice['asset_id'], cache / 'models', '1k', stop), props_folder / 'models')
        except scene_props.PropError as exc:
            if str(exc) == '已取消': raise InterruptedError() from None
            skipped.append({'concept': choice['concept'], 'reason': 'download_failed', 'asset_id': choice['asset_id']}); continue
        props.append({'id': 'p%d' % (len(props) + 1), 'asset_id': record['asset_id'], 'name': record['name'], 'file': record['file'],
                      'zone': choice['zone'], 'pose': choice['pose'], 'license': record['license'],
                      'source_url': record['source_url'], 'provider': scene_props.PROVIDER, 'concept': choice['concept']})
    if not props:
        raise ValueError('道具模型下载失败，当前方案未修改。请检查网络后重试。')
    say('准备地面与背景')
    textures = {}
    try:
        textures = scene_props.catalogue('textures')
    except scene_props.PropError:
        pass
    ground = _surface('floor', brief['setting'], cache / 'surfaces', textures, stop)
    wall = _surface('wall', brief['setting'], cache / 'surfaces', textures, stop)
    for item in (ground, wall):
        if item.get('textures'):
            item.update(scene_props.adopt({'textures': item['textures']}, props_folder / 'surfaces'))
    wall['distance_m'] = round(max(.3, min(1.4, size_mm / 1000 * 1.6)), 3)
    say('布置场景')
    hero = next((shot for shot in plan['shots'] if shot['id'] == 'hero'), plan['shots'][0])
    shot = {'id': SHOT_ID, 'label': '使用场景', 'kind': 'full', 'style': 'soft', 'projection': 'perspective',
            'azimuth': hero.get('azimuth', 35) if abs(hero.get('azimuth', 35)) <= 70 else 35,
            'elevation': max(20, min(30, hero.get('elevation', 20) + 4)), 'focal_length': 55}
    plan['shots'] = [item for item in plan['shots'] if item['id'] != SHOT_ID]
    if len(plan['shots']) >= 12:
        raise ValueError('镜头数量已达上限，请先删除一个镜头。')
    plan.pop('scene_set', None); plan.pop('scene_brief', None)
    plan.get('scene_checkpoints', {}).pop(SHOT_ID, None); plan.get('shot_adjustments', {}).pop(SHOT_ID, None)
    units = 2000 / size_mm
    folder = folder or (DATA / project['id'] / 'scene' / uuid.uuid4().hex)
    result = _layout(project, plan, shot, props, units, seed, brief['open'], folder, 36, wall['distance_m'])
    rows = {row['id']: row for row in result['props']}
    placed = []
    for prop in props:
        row = rows.get(prop['id'], {})
        if not row.get('placed'):
            skipped.append({'concept': prop['concept'], 'reason': row.get('reason') or 'no_room', 'asset_id': prop['asset_id']}); continue
        placed.append({**prop, 'zone': row['zone_used'], 'matrix': row['matrix'], 'size_mm': row['size_mm']})
    # No prop found room: the floor and wall of the setting still make a scene, and the record says what was left out.
    for index, prop in enumerate(placed):
        prop['id'] = 'p%d' % (index + 1)
    shot.update({key: value for key, value in result['camera'].items() if key in ('camera_distance', 'ortho_scale', 'target_offset', 'elevation')})
    plan['shots'].append(shot)
    counts = {}
    for prop in placed:
        counts[prop['zone']] = counts.get(prop['zone'], 0) + 1
    words = {'on_top': '台面', 'inside': '抽屉内', 'beside': '旁边', 'behind': '后方'}
    listed = '、'.join('%s %d 件' % (words[zone], counts[zone]) for zone in scene_set.ZONES if zone in counts) or '仅场景环境，道具未能放入'
    summary = brief.get('summary') or '%s：%s' % (brief['label'], listed)
    plan['scene_set'] = {'version': 1, 'shot_id': SHOT_ID, 'setting': brief['setting'], 'source': source, 'summary': summary[:400],
                         'product': {'type': str(brief['label'])[:120], 'max_dimension_mm': round(size_mm, 1), 'size_basis': basis},
                         'units_per_meter': units, 'props': placed, 'open': result.get('open'), 'surface': ground, 'wall': wall,
                         'basis': scene_set.basis(plan), 'seed': seed, 'skipped': skipped[:40]}
    if proposal:
        plan['scene_brief'] = _brief(proposal, 'render')
    plan = planner.validate(plan)
    return plan, {'summary': summary, 'source': source, 'method': 'render', 'setting': brief['setting'], 'placed': len(placed), 'skipped': skipped,
                  'opened': bool(result.get('open')), 'size_basis': basis, 'max_dimension_mm': round(size_mm, 1),
                  'props': [{key: prop[key] for key in ('id', 'asset_id', 'name', 'zone', 'source_url', 'license')} for prop in placed]}


def generate(project, proposal, hint='', cancelled=None, progress=None, folder=None):
    """A scene made by the image model from renders of the product: returns (plan, record, picture)."""
    import scene_imagegen
    plan = copy.deepcopy(project['plan'])
    plan['shots'] = [item for item in plan['shots'] if item['id'] != SHOT_ID]
    if len(plan['shots']) >= 12:
        raise ValueError('镜头数量已达上限，请先删除一个镜头。')
    plan.pop('scene_set', None); plan.pop('scene_brief', None)
    plan.get('scene_checkpoints', {}).pop(SHOT_ID, None); plan.get('shot_adjustments', {}).pop(SHOT_ID, None)
    azimuth, elevation = _view(plan, proposal.get('azimuth'), proposal.get('elevation'))
    size_mm, basis = real_size(project, recipe_for(project, hint)['size_mm'], proposal.get('max_dimension_mm'))
    scene = proposal.get('scene') or ''
    if hint and proposal.get('source') != 'ai':
        scene = (scene + ' ' + hint).strip()     # the strategist already read the hint; rules have not
    folder = folder or (DATA / project['id'] / 'scene_ai' / uuid.uuid4().hex / 'scene')
    # A size guessed from the category alone is not worth telling the image model.
    picture = scene_imagegen.create(project, plan, azimuth, elevation, scene, proposal.get('product') or project.get('name'),
                                    size_mm if basis != 'category' else None, folder, cancelled, progress)
    plan['shots'].append({'id': SHOT_ID, 'label': '使用场景', 'kind': 'full', 'style': 'soft', 'projection': 'perspective',
                          'azimuth': azimuth, 'elevation': elevation, 'focal_length': 55})
    plan['scene_brief'] = _brief(proposal, 'generate')
    plan = planner.validate(plan)
    made = picture['record']
    record = {'summary': _line(proposal.get('summary'), 400) or '由图像模型生成的使用场景', 'source': proposal.get('source', 'rules'),
              'method': 'generate', 'model': made['model'], 'attempts': made['attempts'], 'check': made['check'],
              'size_basis': basis, 'max_dimension_mm': round(size_mm, 1), 'width': picture['width'], 'height': picture['height']}
    return plan, record, picture


def _proposal(project, task, image, say):
    """The proposal the user last saw, if it still answers this request; a new one otherwise."""
    stored = project.get('scene_proposal'); hint = task.get('hint', '')
    audience = task.get('audience') or 'auto'; reference = task.get('reference')
    if (isinstance(stored, dict) and stored.get('version') == project['version'] and stored.get('hint') == hint
            and audience in (stored.get('audience'), stored.get('segment_id'))
            and reference in (None, stored.get('reference'), stored.get('reference_id'))):
        return stored
    say('推荐目标人群与场景')
    return propose(project, hint, audience, reference, image)


def _audience_record(proposal):
    chosen = scene_audience.segment(proposal.get('segment_id')) if proposal.get('segment_id') else None
    return {'segment_id': proposal.get('segment_id'), 'label': chosen['label'] if chosen else None,
            'reference_id': proposal.get('reference_id'), 'source': proposal.get('source'), 'reason': proposal.get('reason')}


def _run(task_id):
    with LOCK:
        try: task = get(KIND, task_id)
        except KeyError: return
        if task['status'] != 'queued': return
        RUNNING_TASKS.add(task_id)
    try:
        _update(task_id, status='running', stage='识别产品与使用场景', started_at=time.time())
        project = get('project', task['project_id'])
        def cancelled():
            return get(KIND, task_id)['status'] == 'cancelled'
        say = lambda stage: _update(task_id, stage=stage)
        proposal = with_method(_proposal(project, task, _source_image(project), say), task.get('method'))
        picture = None
        if proposal['method'] == 'generate':
            plan, record, picture = generate(project, proposal, task.get('hint', ''), cancelled, say,
                                             DATA / project['id'] / 'scene_ai' / task_id / 'scene')
        else:
            plan, record = build(project, task.get('hint', ''), task.get('seed'), cancelled, say, proposal=proposal)
        record.update(audience=_audience_record(proposal), method_note=proposal.get('method_note'))
        with LOCK:
            current = get('project', task['project_id'])
            if get(KIND, task_id)['status'] == 'cancelled': return
            if current['version'] != task['base_version']:
                raise ValueError('方案在布置场景期间已被修改，本次场景未保存，请重新生成。')
            if any(job['project_id'] == current['id'] and job['status'] in ('queued', 'running')
                   and job.get('origin') not in (engine.POLISH_ORIGIN, engine.THUMBNAIL_ORIGIN) for job in all_records('job')):
                raise ValueError('已有渲染任务开始执行，本次场景未保存，请稍后重试。')
            current.setdefault('history', []).append({'version': current['version'], 'plan': copy.deepcopy(current['plan']),
                                                      'at': time.time(), 'reason': '生成使用场景'})
            current['plan'] = plan; current['version'] += 1
            version = current['version']
            if picture:
                # The picture is this shot's image for this version of the plan, like a render would be.
                url = lambda path: '/files/' + Path(path).relative_to(DATA).as_posix()
                current.setdefault('outputs', []).append(dict(
                    shot_id=SHOT_ID, shot_label='使用场景', quality=GENERATED, version=version, job_id=task_id,
                    png_url=url(picture['png']), jpg_url=url(picture['jpg']), blend_url=None, blend_sha256=None,
                    width=picture['width'], height=picture['height'], checks={}, metrics={}, device=None, created_at=time.time(),
                    result_url=None, effective_plan=engine.effective_plan(plan, SHOT_ID), generated=picture['record']))
            put('project', current)
            job = None
            if not picture:
                try:
                    job = engine.submit(current['id'], 'preview', 'preview', [SHOT_ID], request_key='scene:' + task_id,
                                        expected_version=version)
                except (ValueError, OSError):
                    pass
            task = get(KIND, task_id)
            task.update(status='completed', stage='场景已生成，正在预览' if job else '场景图已生成' if picture else '场景已生成',
                        result=record, applied_version=version, job_id=job['id'] if job else None, finished_at=time.time())
            put(KIND, task)
    except InterruptedError:
        pass
    except Exception as exc:
        with LOCK:
            task = get(KIND, task_id)
            if task['status'] != 'cancelled':
                task.update(status='failed', stage='场景未生成', error=str(exc)[:500], finished_at=time.time()); put(KIND, task)
    finally:
        with LOCK: RUNNING_TASKS.discard(task_id)


def public(task):
    keys = ('id', 'project_id', 'status', 'stage', 'error', 'result', 'applied_version', 'job_id', 'created_at', 'finished_at', 'base_version')
    return {key: copy.deepcopy(task[key]) for key in keys if key in task}


def latest(project_id):
    tasks = [task for task in all_records(KIND) if task.get('project_id') == project_id]
    return public(max(tasks, key=lambda task: task.get('created_at', 0))) if tasks else None


def _choices(body):
    """The user's own choices in a request: (hint, audience, reference, method), each validated."""
    if not isinstance(body, dict): raise ValueError('场景请求格式无效')
    hint = body.get('hint', '')
    if not isinstance(hint, str) or len(hint) > MAX_HINT: raise ValueError('场景说明不能超过 %d 字' % MAX_HINT)
    audience = body.get('audience') or 'auto'
    if not isinstance(audience, str) or (audience != 'auto' and not scene_audience.segment(audience)):
        raise ValueError('所选人群不在参考库中。')
    reference = body.get('reference') or None
    if reference is not None and (audience == 'auto' or not isinstance(reference, str) or not scene_audience.reference(reference, audience)):
        raise ValueError('所选参考不属于这个人群。')
    method = body.get('method') or 'auto'
    if method not in ('auto',) + scene_audience.METHODS: raise ValueError('场景生成方式无效')
    return hint.strip(), audience, reference, None if method == 'auto' else method


def recommend(project_id, body):
    """Ask the strategist now, and keep its answer with the project so the next scene request can use it."""
    hint, audience, reference, method = _choices(body)
    project = get('project', project_id)
    if not project.get('analysis') or project.get('status') != 'ready': raise ValueError('请先等待模型分析完成')
    proposal = with_method(propose(project, hint, audience, reference, _source_image(project)), method)
    with LOCK:
        current = get('project', project_id)
        current['scene_proposal'] = proposal; put('project', current)
    return proposal


def submit(project_id, body):
    hint, audience, reference, method = _choices(body)
    expected = body.get('expected_version')
    if expected is not None and (type(expected) is not int or expected < 1): raise ValueError('方案版本无效')
    seed = body.get('seed')
    if seed is not None and (type(seed) is not int or not 0 < seed < 10 ** 9): raise ValueError('随机种子无效')
    with LOCK:
        project = get('project', project_id)
        if not project.get('analysis') or project.get('status') != 'ready': raise ValueError('请先等待模型分析完成')
        if expected is not None and project['version'] != expected: raise engine.JobConflictError('方案已更新，请重新载入后生成场景。')
        if not engine.blender_path(): raise ValueError('未找到 Blender，无法布置场景。')
        if method == 'generate': with_method({}, method)       # refuse now, not after the queue, when no image model can be called
        engine.yield_to_user(project_id)
        if any(job['project_id'] == project_id and job['status'] in ('queued', 'running') for job in all_records('job')):
            raise engine.JobConflictError('当前项目已有渲染任务，请等待完成或取消后再生成场景。')
        if any(task['project_id'] == project_id and task['status'] in ACTIVE for task in all_records(KIND)):
            raise engine.JobConflictError('场景正在生成，请稍候。')
        task = {'id': uuid.uuid4().hex, 'project_id': project_id, 'status': 'queued', 'stage': '等待开始', 'error': None,
                'hint': hint, 'audience': audience, 'reference': reference, 'method': method, 'seed': seed,
                'base_version': project['version'], 'created_at': time.time()}
        put(KIND, task)
        threading.Thread(target=_run, args=(task['id'],), daemon=True, name='scene-' + task['id'][:8]).start()
        return public(task)


def cancel(project_id, task_id):
    with LOCK:
        task = get(KIND, task_id)
        if task.get('project_id') != project_id: raise KeyError(task_id)
        if task['status'] in ACTIVE:
            task.update(status='cancelled', stage='已取消，方案未修改', error=None, finished_at=time.time()); put(KIND, task)
        return public(task)


def remove(project_id, expected_version=None):
    """Take the usage scene out of the plan again, as one undoable version."""
    with LOCK:
        project = get('project', project_id)
        if expected_version is not None and project['version'] != expected_version:
            raise engine.JobConflictError('方案已更新，请重新载入后再试。')
        plan = copy.deepcopy(project['plan'])
        if 'scene_set' not in plan and 'scene_brief' not in plan and not any(shot['id'] == SHOT_ID for shot in plan['shots']):
            return project
        engine.yield_to_user(project_id)
        plan.pop('scene_set', None); plan.pop('scene_brief', None)
        plan['shots'] = [shot for shot in plan['shots'] if shot['id'] != SHOT_ID] or plan['shots']
        plan.get('scene_checkpoints', {}).pop(SHOT_ID, None); plan.get('shot_adjustments', {}).pop(SHOT_ID, None)
        plan = planner.validate(plan)
        project.setdefault('history', []).append({'version': project['version'], 'plan': copy.deepcopy(project['plan']),
                                                  'at': time.time(), 'reason': '移除使用场景'})
        project['plan'] = plan; project['version'] += 1; put('project', project)
        return project


def recover_interrupted():
    for task in all_records(KIND):
        if task.get('status') in ACTIVE:
            task.update(status='failed', stage='场景未生成', error='应用重启，场景生成被中断；方案未修改，可重新生成。'); put(KIND, task)

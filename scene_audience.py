"""Who a usage scene is for: audience segments and the visual references behind them.

references/eu-tool-ecommerce lists, for eight kinds of tool buyer, ten published
images each: where the image is and what it does well. The pack holds links and
written observations only, never the images themselves; its README states the
copyright terms. Here the pack becomes data the scene agents can read:

    library()         the segments, their buying question and their references
    rank()            which segment a product most plausibly belongs to, by words
    propose_rules()   a scene proposal without any language model
    validate_brief()  the record of the choice that is stored in the plan

References are composition notes. Nothing in this module downloads, shows or
forwards a third-party image, and brand names never reach an image prompt.
"""
import csv
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACK_ID = 'eu-tool-ecommerce'
PACK = ROOT / 'references' / PACK_ID
PACK_TITLE = '欧洲工具电商视觉参考'
PACK_ACCESSED = '2026-11-13'
METHODS = ('render', 'generate')
SOURCES = ('ai', 'rules', 'user')
MAX_TEXT = 400

# The pack's strategy matrix (its README), in the order of its index. ``words``
# are product words that point at the segment when no model is there to judge;
# ``scene`` is the picture to ask for when no model has written a better one.
SEGMENTS = (
    {'id': 'home_starter', 'scene': 'A bright, tidy room at home. A person in casual clothes uses the product for a simple household fix; its accessories lie neatly within reach.',
     'label': '家庭轻维修与入门用户', 'question': '会不会用、买到什么',
     'priority': '套装全貌、手持尺度、家庭任务、步骤', 'focus': '明亮、简单、配件完整',
     'words': ('螺丝刀', '批头', '家用', '入门', '挂画', '手电钻', '电钻', '工具套装', '套装', 'screwdriver', 'bit set',
               'cordless drill', 'starter', 'household', 'home kit')},
    {'id': 'diy_project', 'scene': 'A home improvement project under way in a garage: timber boards on trestles, a homeowner in work gloves using the product, the half-finished piece visible behind.',
     'label': '项目型 DIY 屋主', 'question': '能完成什么项目、是否兼容',
     'priority': '项目操作、成果、材料、附件', 'focus': '任务过程、应用范围',
     'words': ('曲线锯', '喷涂', '喷枪', '搁架', '层板', '装修', '翻新', 'diy', 'jigsaw', 'spray gun', 'paint sprayer',
               'shelf', 'renovation')},
    {'id': 'workshop_hobbyist', 'scene': 'A well-kept private workshop with a solid wooden workbench and hand tools on the wall. An enthusiast works carefully with the product; measuring tools on the bench.',
     'label': '进阶爱好者与个人工坊用户', 'question': '精度和结构是否适合我',
     'priority': '金属细节、刻度、工作台操作', 'focus': '加工质量、可控性、尺寸',
     'words': ('砂磨', '砂带', '打磨机', '工作台', '台钳', '木工', '工坊', '车床', '雕刻', 'sander', 'workbench', 'vise',
               'woodwork', 'lathe', 'router', 'bench')},
    {'id': 'mobile_tradesperson', 'scene': 'A tradesperson on a house call: the open rear of a plain work van on a residential street, the product within reach as they set up for the job.',
     'label': '自雇技工与上门维修者', 'question': '是否便携、能否适应工况',
     'priority': '现场操作、手握、腰包、装车', 'focus': '真实尺度、使用姿势、可靠性',
     'words': ('工具包', '工具箱', '腰包', '便携', '上门', '维修', '锯片', '钻头', '开孔器', '凿', 'tool bag', 'toolbox',
               'tool box', 'portable', 'blade', 'hole saw', 'chisel', 'drill bit')},
    {'id': 'site_team', 'scene': 'A small renovation jobsite: a bare plastered room with a stepladder and dust sheets. Two installers work with the product between them, one taking a tool from it.',
     'label': '小型施工与安装团队', 'question': '如何运输、配置和管理',
     'priority': '堆叠、抽屉、车载、整套布局', 'focus': '系统兼容、取用、协作',
     'words': ('工具车', '工具柜', '抽屉柜', '堆叠', '收纳箱', '车载', '电锤', '角磨', '圆锯', '斜切锯', '激光', 'tool cart',
               'tool cabinet', 'tool chest', 'trolley', 'stackable', 'storage system', 'hammer drill', 'grinder',
               'miter saw', 'laser level')},
    {'id': 'industrial_maintenance', 'scene': 'A clean industrial plant room with pipework and control cabinets. A maintenance technician in a plain work jacket uses the product during an inspection.',
     'label': '专业维修与工业维护采购', 'question': '型号是否适配、如何选型',
     'priority': '套筒排列、接口、规格、技术细节', 'focus': '明确尺寸与边界、可核查信息',
     'words': ('万用表', '测试仪', '仪表', '套筒', '扭力', '探头', '钳形表', '规格', 'multimeter', 'tester', 'socket set',
               'torque', 'probe', 'clamp meter', 'gauge')},
    {'id': 'home_garden', 'scene': 'A small private garden on a mild day: a trimmed lawn, a hedge and a wooden shed. A homeowner uses the product at an easy pace.',
     'label': '家庭园艺与庭院维护者', 'question': '是否适合空间、是否好操作',
     'priority': '修剪动作、庭院尺度、收纳', 'focus': '任务清晰、轻松、实际比例',
     'words': ('割草机', '绿篱', '修枝', '灌木剪', '园艺', '花园', '庭院', '草坪', '浇水', 'mower', 'hedge', 'trimmer',
               'garden', 'lawn', 'pruner', 'shears', 'watering')},
    {'id': 'pro_landscape', 'scene': 'A public park in the morning: wide lawns and mature trees. A groundskeeper in plain protective workwear operates the product, a service vehicle parked far behind.',
     'label': '专业园林与绿地维护者', 'question': '大面积作业如何安排',
     'priority': '工作现场、设备组合、操作细节', 'focus': '工作流程、防护、补能与维护',
     'words': ('割灌机', '电锯', '链锯', '高枝锯', '吹风机', '园林', '绿地', '绿化', 'brushcutter', 'chainsaw', 'pole saw',
               'blower', 'landscap')},
)
# A reference whose picture shows the product at work, usually in someone's hands.
IN_USE = re.compile(r'使用|操作|锯切|标记|探测|组装|拧|钻孔|喷涂|工作现场|工作场景|排查|用[^，。；：]{1,14}(?:打磨|处理)|mowing|on the job|troubleshooting', re.I)
_LOCK = threading.Lock()
_CACHE = {}


def _rows():
    path = PACK / 'references.csv'
    try:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = [row for row in csv.DictReader(stream) if isinstance(row, dict)]
        stamp = path.stat().st_mtime_ns
    except (OSError, UnicodeError, csv.Error):
        return [], None
    return rows, stamp


def _clean(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def library():
    """The pack as data: segments in index order, each with its numbered references.

    A missing or unreadable pack gives no segments: scenes then work as they did
    before audiences existed.
    """
    rows, stamp = _rows()
    with _LOCK:
        if _CACHE.get('stamp') == stamp and 'segments' in _CACHE:
            return _CACHE['segments']
        grouped = {}
        for row in rows:
            url = _clean(row.get('url'), 500)
            if not re.fullmatch(r'https://[^\s<>"\']+', url):
                continue                    # only real web links are ever shown or stored
            grouped.setdefault(_clean(row.get('segment'), 80), []).append(
                {'title': _clean(row.get('title'), 80), 'url': url, 'locator': _clean(row.get('locator'), 200),
                 'attribution': _clean(row.get('attribution'), 120), 'note': _clean(row.get('note'), 200)})
        segments = []
        for number, base in enumerate(SEGMENTS, 1):
            references = []
            for index, item in enumerate(grouped.get(base['label'], [])[:20], 1):
                text = ' '.join((item['title'], item['locator'], item['note']))
                references.append({'id': '%02d-%02d' % (number, index), **item, 'in_use': bool(IN_USE.search(text))})
            if references:
                segments.append({**{key: base[key] for key in ('id', 'label', 'question', 'priority', 'focus', 'scene')},
                                 'number': number, 'references': references})
        _CACHE.update(stamp=stamp, segments=segments)
        return segments


def segment(segment_id):
    return next((item for item in library() if item['id'] == segment_id), None)


def reference(reference_id, segment_id=None):
    """A reference by its number, optionally only if it belongs to the given segment."""
    for item in library():
        if segment_id in (None, item['id']):
            for row in item['references']:
                if row['id'] == reference_id:
                    return row
    return None


def public():
    """What the interface shows: every segment and reference, with where the pack can be read."""
    segments = library()
    return {'pack': {'id': PACK_ID, 'title': PACK_TITLE, 'accessed': PACK_ACCESSED, 'count': sum(len(s['references']) for s in segments),
                     'index_url': '/references/%s/index.html' % PACK_ID if (PACK / 'index.html').is_file() else None},
            'segments': segments}


def rank(text, strong=''):
    """Segments ordered by how well their words describe the product; unmatched ones are left out."""
    weak = str(text or '').casefold(); strong = str(strong or '').casefold()
    available = {item['id'] for item in library()}
    scored = []
    for base in SEGMENTS:
        if base['id'] not in available:
            continue
        # As with scene recipes, a longer matching word says more than a short one.
        score = sum(2 + min(len(word), 12) / 4 for word in base['words'] if word in strong) + sum(1 for word in base['words'] if word in weak)
        if score:
            scored.append((score, base['id']))
    return [segment_id for _, segment_id in sorted(scored, key=lambda row: -row[0])]


def brands():
    """Names of the publishers in the pack; an image prompt must never carry them."""
    names = set()
    for item in library():
        for row in item['references']:
            names.update(re.findall(r'[A-Za-z]{4,}', row['attribution'].split('/')[0].split('（')[0]))
    return sorted(names - {'Power', 'Tools', 'Professional'})


def scrub(text):
    """Scene wording without publisher names from the pack."""
    for name in brands():
        text = re.sub(r'(?i)\b%s\b' % re.escape(name), '', text)
    return re.sub(r'\s{2,}', ' ', text).strip()


def pick_reference(segment_id, in_use):
    """The first reference of a segment that does (or does not) show the product in use."""
    chosen = segment(segment_id)
    if not chosen:
        return None
    return next((row for row in chosen['references'] if row['in_use'] == bool(in_use)), chosen['references'][0])


def propose_rules(text, strong='', required=None, recipe_segment=None):
    """A proposal from words alone: (segment id or None, reference or None, reason)."""
    if required and segment(required):
        chosen = segment(required)
        return chosen['id'], pick_reference(chosen['id'], False), '按你选择的人群「%s」。' % chosen['label']
    ranked = rank(text, strong)
    if not ranked and recipe_segment and segment(recipe_segment):
        ranked = [recipe_segment]
    if not ranked:
        return None, None, '参考库面向工具、工坊与园林设备；按名称和部件没有判断出对应的人群，将按通用场景处理。可以手动选择人群。'
    chosen = segment(ranked[0])
    return chosen['id'], pick_reference(chosen['id'], False), '按产品名称和部件判断，购买者最接近「%s」：他们关心%s。' % (chosen['label'], chosen['question'])


def brief_text(segment_id, reference_id=None):
    """What a scene agent is told about the chosen audience, in plain fields."""
    chosen = segment(segment_id)
    if not chosen:
        return None
    facts = {'buyer': chosen['label'], 'buying_question': chosen['question'], 'images_that_answer_it': chosen['priority'],
             'emphasis': chosen['focus']}
    row = reference(reference_id, segment_id) if reference_id else None
    if row:
        # The observation only. Titles and locators name brands and models, which a scene must not copy.
        facts['reference_observation'] = row['note']
    return facts


def validate_brief(value, plan):
    """Raise ValueError unless ``value`` is a well-formed plan['scene_brief']."""
    allowed = {'version', 'shot_id', 'segment_id', 'reference_id', 'source', 'method', 'reason', 'summary', 'people', 'complex'}
    if not isinstance(value, dict) or set(value) - allowed or value.get('version') != 1:
        raise ValueError('场景人群记录无效')
    if value.get('shot_id') not in {shot['id'] for shot in plan.get('shots', [])}:
        raise ValueError('场景人群记录必须对应一个现有镜头')
    if value.get('segment_id') is not None and not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', str(value['segment_id'])):
        raise ValueError('场景人群标识无效')
    if value.get('reference_id') is not None and not re.fullmatch(r'[0-9]{2}-[0-9]{2}', str(value['reference_id'])):
        raise ValueError('场景参考标识无效')
    if value.get('source') not in SOURCES or value.get('method') not in METHODS:
        raise ValueError('场景人群记录的来源或生成方式无效')
    for key in ('reason', 'summary'):
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > MAX_TEXT or any(ord(char) < 32 for char in text):
            raise ValueError('场景人群记录的说明无效')
    for key in ('people', 'complex'):
        if type(value.get(key, False)) is not bool:
            raise ValueError('场景人群记录的标记无效')
    return value


def image_shots(plan):
    """Shots whose picture comes from the image model, not from Blender."""
    brief = plan.get('scene_brief') if isinstance(plan, dict) else None
    if isinstance(brief, dict) and brief.get('method') == 'generate' and isinstance(brief.get('shot_id'), str):
        return {brief['shot_id']}
    return set()

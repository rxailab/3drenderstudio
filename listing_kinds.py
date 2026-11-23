"""The pages of a listing set: the template kinds, their canvas, the fixed words they print, and which pages a product has
what it needs for (``requirements``). Split out of ``brandkit_set``, which still re-exports every name.
"""

# kind: (zh, en)
KINDS = {
    'T12': ('海报主图', 'Hero poster'),
    'T01': ('功能标注', 'Callouts'),
    'T03': ('主体 + 细节栏', 'Product + details'),
    'T13': ('多姿态展示', 'Poses'),
    'T02': ('深色细节特写', 'Close-ups'),
    'T04': ('场景 + 参数带', 'Scene + key figures'),
    'T08': ('使用场景组图', 'Usage scenes'),
    'T06': ('尺寸图', 'Dimensions'),
    'T05': ('包装清单', 'In the box'),
    'T07': ('模块系统', 'Modules'),
    'T09': ('同条件对比', 'Comparison'),
    'T10': ('接口与连接', 'Connections'),
    'T11': ('同系列变体', 'Variants'),
}
ORDER = tuple(KINDS)
AUTO = ('T12', 'T01', 'T03', 'T13', 'T02', 'T04', 'T08', 'T06', 'T05')
# Pages that need facts a model does not carry. They are listed so the user sees why they are not made.
BLOCKED = {'T07': '需要每个模块单独的模型和它们的叠放层级', 'T09': '需要同条件测试的对比证据', 'T10': '需要已确认的连接关系',
           'T11': '需要同系列其他型号的模型'}
CANVAS = {'1:1': (1500, 1500), '4:5': (1200, 1500)}
ASPECTS = tuple(CANVAS)
TEXT = {
    'zh': {'T01_eyebrow': '部件一览', 'T01_headline': '每个关键部件，\n一眼看清', 'T03_eyebrow': '细节', 'T02_eyebrow': '细节特写',
           'T02_headline': '近看细节', 'T04_headline': '核心参数', 'T04_eyebrow': '参数', 'T08_eyebrow': '使用场景', 'T08_headline': '用在需要它的地方',
           'T06_eyebrow': '尺寸', 'T06_headline': '外形尺寸', 'T05_headline': '包装清单', 'T05_note': '图示物品未按实际比例',
           'qty_pending': '数量待确认', 'qty_prefix': '数量 ', 'main': '主件', 'height': '高度', 'width': '宽度', 'length': '长度', 'depth': '深度', 'thickness': '厚度', 'diameter': '直径',
           'table_note': '尺寸为近似值，以实物为准。', 'depth_note': '深度 %s。',
           'T13_eyebrow': '活动部件', 'T13_headline': '一件工具，多种状态', 'T13_from_to': '从%s\n[[到%s]]',
           'scene_titles': ('上手使用', '随时待命', '换个场景')},
    'en': {'T01_eyebrow': 'Part overview', 'T01_headline': 'Every key part,\nclearly shown', 'T03_eyebrow': 'Details', 'T02_eyebrow': 'Up close',
           'T02_headline': 'The details, up close', 'T04_headline': 'Key figures', 'T04_eyebrow': 'Specs', 'T08_eyebrow': 'In use',
           'T08_headline': 'Made for the job', 'T06_eyebrow': 'Dimensions', 'T06_headline': 'Overall size', 'T05_headline': "What's in the box",
           'T05_note': 'Items not shown to scale', 'qty_pending': 'Qty to be confirmed', 'qty_prefix': 'Qty ', 'main': 'Main unit',
           'height': 'Height', 'width': 'Width', 'length': 'Length', 'depth': 'Depth', 'thickness': 'Thickness', 'diameter': 'Diameter',
           'table_note': 'Sizes are approximate; actual product may vary slightly.', 'depth_note': 'Depth %s.',
           'T13_eyebrow': 'Moving parts', 'T13_headline': 'One tool, every position', 'T13_from_to': '%s\n[[to %s]]',
           'scene_titles': ('In use', 'Ready to go', 'On the job')},
}


def anchored(product, whole=None):
    """Indexes of the selling points that name parts of the model (they can be pointed at and shown close up). A selling
    point that names every part of the model (a model in one piece) is about the whole product, not a part of it."""
    whole = set(whole or product.get('_model_parts') or ())
    return [index for index, feature in enumerate(product['features'])
            if feature.get('parts') and not (whole and set(feature['parts']) >= whole)]


def pointed(product):
    """Indexes of the selling points that were found on the main picture (``point``) without a part of their own."""
    have = set(anchored(product))
    return [index for index, feature in enumerate(product['features']) if index not in have and feature.get('point')]


NO_FEATURES = '没有填写卖点。请在「产品信息」里写下卖点（或粘贴产品介绍后点「提取到各栏」），这一页才会生成'


def requirements(product, parts, size_known, scene_supply, poses=0):
    """(has, why): whether each page has what it needs, and the reason when it does not. ``poses``: how many states of its
    moving parts the product can be shown in (the pose agent's, ``articulation.states``) or pose pictures there are."""
    points = len(anchored(product))
    callouts = len([index for index in anchored(product) if not product['features'][index].get('no_leader')]) + len(pointed(product))
    figures = bool(product['figures'] or product['specs'] or size_known)
    has = {'T12': True, 'T01': callouts >= 2, 'T03': points >= 2, 'T13': poses >= 2, 'T02': points >= 1, 'T04': scene_supply >= 1 and figures,
           'T08': scene_supply >= 2, 'T06': size_known, 'T05': bool(parts), 'T07': False, 'T09': False, 'T10': False, 'T11': False}
    single = len(set(product.get('_model_parts') or ())) == 1
    why = {'T01': '至少 2 条卖点需要选择对应部件，或配置视觉模型在主图上找出它们的位置',
           'T03': '模型是一个整体，没有可单独特写的部件' if single else '至少 2 条卖点需要选择对应部件',
           'T02': '模型是一个整体，没有可单独特写的部件' if single else '卖点没有选择对应的部件',
           'T04': '需要场景图（开启 AI 场景或工件图），以及规格、大数字指标或真实尺寸' if not figures else '需要场景图：请开启 AI 场景图或工件使用图',
           'T08': '需要至少 2 张场景图：请开启 AI 场景图（2 张以上）或工件使用图', 'T06': '没有填写真实尺寸', 'T05': '还没有处理完成的组件',
           'T13': '没有识别出可以展示的活动部件（需要至少 2 个姿态；可在「活动部件」里查看或重新识别）', **BLOCKED}
    if not any(str(row.get('title') or '').strip() for row in product['features']):
        # Nothing the seller wrote to print: the pages that print selling points wait for them; none is made up.
        why.update(T01=NO_FEATURES, T02=NO_FEATURES, T03=NO_FEATURES)
    return has, why

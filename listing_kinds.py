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


class Reason(str):
    """Why a page cannot be made: the words themselves (every reader that printed a plain reason still does), plus where
    the seller supplies what is missing. ``needs`` lists every such place as {'step': 'product' | 'brand', 'field'},
    ``fix`` is the first of them, and ``hard`` says that nothing the seller can do in this app fixes it."""
    def __new__(cls, why, *needs, hard=False):
        self = super().__new__(cls, why)
        self.hard = bool(hard) or not needs
        self.needs = [] if self.hard else [{'step': step, 'field': field} for step, field in needs]
        self.fix = self.needs[0] if self.needs else None
        return self

    def public(self):
        return {'why': str(self), 'fix': self.fix, 'needs': list(self.needs), 'hard': self.hard}


def reason_public(value):
    """A reason as the workspace reads it: {'why', 'fix', 'needs', 'hard'}. A plain string says nothing about a fix."""
    if isinstance(value, Reason):
        return value.public()
    return {'why': str(value), 'fix': None, 'needs': [], 'hard': True}


# Where things are filled in, numbered as the workspace shows its steps (选风格, 选图片, 填产品, 品牌与场景, 生成).
PRODUCT, BRAND = '第 3 步「填产品」', '第 4 步「品牌与场景」'
NO_FEATURES = '没有填写卖点：在%s的「卖点」里添加，或粘贴产品介绍后点「提取到各栏」' % PRODUCT
NEED_PARTS = '至少 %d 条卖点需要选「对应部件」：在%s每条卖点下的「对应部件」里勾选它说的部件'
ONE_PIECE = '模型是一个整体，没有可单独特写的部件'
# The image model is set in the server's .env file only; the in-app AI 设置 has no field for it.
NO_IMAGE_MODEL = '没有配置图像模型（在服务器的 .env 文件里填写 AI_IMAGE_BASE_URL 和 AI_IMAGE_MODEL，「AI 设置」里不能设置）'
USECASE = '在%s的「工件使用图」里勾选「渲染工件使用图」（算 1 张场景图）' % BRAND


def _scene_words(need, scenes):
    """(why there are fewer than ``need`` scene pictures and what really adds them, whether nothing in the app can).
    ``scenes``: {'image_ready', 'enabled', 'count', 'usecase', 'legacy'} (listing.scene_state), or None when unknown."""
    if not scenes:
        return '需要 %d 张场景图：在%s开启「AI 场景图」或「工件使用图」' % (need, BRAND), False
    if not scenes.get('image_ready'):
        if need - scenes.get('legacy', 0) > 1:
            # without the image model one workpiece render is all this app can add
            return '需要 %d 张场景图，但%s，做不了 AI 场景图；工件使用图只算 1 张' % (need, NO_IMAGE_MODEL), True
        return '需要场景图，但%s，做不了 AI 场景图；可以%s' % (NO_IMAGE_MODEL, USECASE), False
    if not scenes.get('enabled'):
        return '需要 %d 张场景图，但 AI 场景图已关闭：在%s的「AI 场景图」里勾选「生成 AI 场景图」' % (need, BRAND), False
    more = '' if scenes.get('usecase') else '，或' + USECASE
    return '需要 %d 张场景图，AI 场景图只设了 %d 张：在%s的「AI 场景图」里调高「场景图数量」%s' % (need, scenes.get('count', 0), BRAND, more), False


def requirements(product, parts, size_known, scene_supply, poses=0, scenes=None, waiting=0, vision=False):
    """(has, why): whether each page has what it needs, and the reason (a ``Reason``) when it does not. ``poses``: how
    many states of its moving parts the product can be shown in (the pose agent's, ``articulation.states``) or pose
    pictures there are. ``scenes``: the scene settings behind ``scene_supply`` (``_scene_words``), so the reason says
    what really blocks; ``waiting``: how many components are still being processed. ``vision``: the vision model is
    set, so a build finds the selling points of a model without parts to pick on the main picture (``locate``); the
    plan counts the callout page as makeable then, and the build says so when nothing was found."""
    points = len(anchored(product))
    callouts = len([index for index in anchored(product) if not product['features'][index].get('no_leader')]) + len(pointed(product))
    figures = bool(product['figures'] or product['specs'] or size_known)
    has = {'T12': True, 'T01': callouts >= 2, 'T03': points >= 2, 'T13': poses >= 2, 'T02': points >= 1, 'T04': scene_supply >= 1 and figures,
           'T08': scene_supply >= 2, 'T06': size_known, 'T05': bool(parts), 'T07': False, 'T09': False, 'T10': False, 'T11': False}
    named = set(product.get('_model_parts') or ())
    single = len(named) == 1
    written = sum(1 for row in product['features'] if str(row.get('title') or '').strip())
    titled = bool(written)
    feat, pick = ('product', 'features'), ('product', 'feature_parts')
    if not titled:
        # Nothing the seller wrote to print: the pages that print selling points wait for them; none is made up.
        t01 = Reason(NO_FEATURES, feat)
    elif len(named) <= 1 and not vision:
        # no part to pick, and no vision model to find the selling points on the main picture: nothing in the workspace places them
        t01 = Reason('模型没有可选的部件，卖点的位置只能由视觉模型在主图上找出，但没有配置视觉模型（在「AI 设置」的「预览图像分析」里配置）', hard=True)
    elif len(named) <= 1 and written < 2:
        t01 = Reason('至少需要 2 条卖点，生成时由视觉模型在主图上找出它们的位置：在%s的「卖点」里再添加' % PRODUCT, feat)
    elif len(named) <= 1:
        # the build asks the vision model where the selling points are on the main picture (``locate``)
        has['T01'] = True
        t01 = Reason('视觉模型没有在主图上找出至少 2 处卖点的位置', hard=True)
    else:
        t01 = Reason(NEED_PARTS % (2, PRODUCT), pick)

    def close(count):
        if single:
            return Reason(ONE_PIECE, hard=True)
        if len(named) < 2:
            # the 对应部件 picker is shown only for a model with 2 or more named parts: nothing to pick
            return Reason('模型没有命名的部件，选不了「对应部件」，做不了部件特写', hard=True)
        return Reason(NO_FEATURES, feat) if not titled else Reason(NEED_PARTS % (count, PRODUCT), pick)
    scene1, hard1 = _scene_words(1, scenes)
    scene2, hard2 = _scene_words(2, scenes)
    figures_words = '在%s填写「最长边真实尺寸（mm）」，或在「更多信息」里添加「规格」或「大数字指标」' % PRODUCT
    if scene_supply < 1 and not figures:
        t04 = Reason('缺两样：一是场景图（%s）；二是参数：%s' % (scene1, figures_words), ('product', 'specs'), ('brand', 'scenes'), hard=hard1)
    elif scene_supply < 1:
        t04 = Reason('参数已有，缺场景图：' + scene1, ('brand', 'scenes'), hard=hard1)
    else:
        t04 = Reason('场景图已有，缺参数：' + figures_words, ('product', 'specs'))
    if waiting:
        t05 = Reason('有 %d 个组件还在处理，处理完成后这一页就能生成（在%s的「套装组件」里查看进度）' % (waiting, PRODUCT), ('product', 'components'))
    else:
        t05 = Reason('还没有处理完成的组件：在%s的「套装组件」里点「添加组件」，上传随箱物品的模型或图片' % PRODUCT, ('product', 'components'))
    why = {'T01': t01, 'T03': close(2), 'T02': close(1), 'T04': t04, 'T08': Reason(scene2, ('brand', 'scenes'), hard=hard2),
           'T06': Reason('没有填写真实尺寸：在%s的「最长边真实尺寸（mm）」里填写' % PRODUCT, ('product', 'size')), 'T05': t05,
           'T13': Reason('没有识别出可以展示的活动部件（需要至少 2 个姿态）：在%s的「活动部件」里点「识别活动部件」' % PRODUCT, ('product', 'articulation')),
           **{kind: Reason(text, hard=True) for kind, text in BLOCKED.items()}}
    return has, why

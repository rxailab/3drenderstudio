"""Marketplace main-image presets used by the e-commerce export.

Values were collected in November 2026 from each marketplace's public seller
help, rule pages and open-platform documents, as surfaced by web search. Where
the official pages disagree or could not be reached, the stricter commonly
published value is used and the entry says so in ``notes``. Marketplaces revise
these rules; the seller backend's own upload prompt is always authoritative.

Fields
    aspects      ratios the main-image slot accepts (render ratios of this app)
    aspect       the ratio exported by default
    min_px       smallest accepted long side; below it the export is flagged
    target_px    long side exported when the render has enough pixels
    max_bytes    file-size ceiling; JPEG quality is lowered until it fits
    white        'required' | 'recommended' | 'optional' pure-white background
    fill_min     smallest product share of the frame the rule demands, if any
    fill_target  product share the smart framing aims for
    white_slot   a separate dedicated white-background image, when the
                 marketplace has one (always pure white, exported as an extra)
"""
import copy

MB = 1024 * 1024
REVIEWED = '2026-11'

PLATFORMS = [
    {
        'id': 'general', 'name': '通用白底主图', 'group': '通用',
        'aspects': ['1:1', '3:4', '4:3'], 'aspect': '1:1',
        'min_px': 800, 'target_px': 2000, 'max_px': None, 'max_bytes': 5 * MB,
        'white': 'recommended', 'fill_min': None, 'fill_target': .85,
        'confidence': 'convention',
        'notes': ['不针对具体平台：方图 2000 px、纯白底、产品约占画面 85%，可满足多数平台的主图要求。'],
        'sources': [],
    },
    {
        'id': 'amazon', 'name': 'Amazon 亚马逊', 'group': '跨境',
        'aspects': ['1:1'], 'aspect': '1:1',
        'min_px': 1000, 'target_px': 2000, 'max_px': 10000, 'max_bytes': 10 * MB,
        'white': 'required', 'fill_min': .85, 'fill_target': .88,
        'confidence': 'official',
        'notes': ['主图必须为纯白底 RGB 255,255,255，产品至少占画面 85%，不得含文字、水印、边框或附加图形。',
                  '最长边至少 500 px，超过 1000 px 才能缩放查看；这里按 1000 px 作为最低要求，默认导出 2000 px。',
                  '官方未规定画幅，方图为通行做法；10 MB 文件上限来自第三方指南。'],
        'sources': ['https://sellercentral.amazon.com/gp/help/external/G9FUUH87RBNXGKB7',
                    'https://sellercentral.amazon.com/gp/help/external/G75PWC4THA8J269P'],
    },
    {
        'id': 'taobao', 'name': '淘宝 / 天猫', 'group': '国内',
        'aspects': ['1:1', '3:4'], 'aspect': '1:1',
        'min_px': 800, 'target_px': 1440, 'max_px': 10000, 'max_bytes': 3 * MB,
        'white': 'optional', 'fill_min': None, 'fill_target': .85,
        'white_slot': {'label': '白底图', 'aspect': '1:1', 'px': 800, 'max_bytes': 300 * 1024,
                       'shadow': False, 'fill_target': .9},
        'confidence': 'mixed',
        'notes': ['主图分 1:1 与 3:4 两种槽位；2025 年规则建议 1440 px（1440×1440 / 1440×1920），800×800 与 750×1000 仍可用。',
                  '2025 年起官方单图上限提高到 20 MB；不少类目与工具仍按 3 MB 校验，这里按 3 MB 压缩。',
                  '白底图是独立槽位：800×800、JPG、纯白底、无阴影无文字，300 KB 以内（来自较早的公开指南）。'],
        'sources': ['https://open.alitrip.com/support/announcementDetail.htm?id=25787'],
    },
    {
        'id': 'jd', 'name': '京东', 'group': '国内',
        'aspects': ['1:1'], 'aspect': '1:1',
        'min_px': 800, 'target_px': 1200, 'max_px': None, 'max_bytes': 1 * MB,
        'white': 'required', 'fill_min': None, 'fill_target': .85,
        'confidence': 'mixed',
        'notes': ['主图 1:1、800×800 以上；首图须为纯白底（服饰、家纺等类目除外），主体居中撑满画面。',
                  '文件上限各渠道规则不一（500 KB 至 3 MB），这里按 1 MB 压缩；不得含水印、文字、边框或拼图。'],
        'sources': ['https://rule.jd.com/b/rule/ruleDetail.action?ruleId=1088351010591739904&btype=11'],
    },
    {
        'id': 'pdd', 'name': '拼多多', 'group': '国内',
        'aspects': ['1:1'], 'aspect': '1:1',
        'min_px': 480, 'target_px': 800, 'max_px': None, 'max_bytes': 1 * MB,
        'white': 'recommended', 'fill_min': None, 'fill_target': .85,
        'confidence': 'secondary',
        'notes': ['轮播主图 1:1、宽高不低于 480 px、1 MB 以内（官方页面未能直接核对，数值来自多份卖家指南）。',
                  '非服饰类目建议首图使用纯白底且不含文字。'],
        'sources': [],
    },
    {
        'id': 'douyin', 'name': '抖音电商', 'group': '国内',
        'aspects': ['1:1', '3:4'], 'aspect': '1:1',
        'min_px': 600, 'target_px': 1200, 'max_px': None, 'max_bytes': 1 * MB,
        'white': 'optional', 'fill_min': None, 'fill_target': .85,
        'white_slot': {'label': '白底图', 'aspect': '1:1', 'px': 800, 'max_bytes': 1 * MB,
                       'shadow': False, 'fill_target': .85},
        'confidence': 'official',
        'notes': ['1:1 主图必填，不低于 600×600；可另传 3:4 主图（不低于 375×500，建议 750×1000）。',
                  '学习中心写 1:1 主图 5 MB 以内，开放平台接口为 1 MB，这里按 1 MB 压缩。',
                  '白底图是独立槽位：纯白无渐变、无阴影、无 Logo，主体居中且占宽或高的 70% 以上。'],
        'sources': ['https://school.jinritemai.com/doudian/web/articlev0/101669',
                    'https://op.jinritemai.com/docs/api-docs/1438/5331'],
    },
    {
        'id': 'xiaohongshu', 'name': '小红书', 'group': '国内',
        'aspects': ['3:4', '1:1'], 'aspect': '3:4',
        'min_px': 1000, 'target_px': 1600, 'max_px': None, 'max_bytes': 2 * MB,
        'white': 'optional', 'fill_min': None, 'fill_target': .8,
        'confidence': 'mixed',
        'notes': ['商品主图支持 1:1（800×800）或 3:4（750×1000）；信息流以 3:4 竖图为主，这里默认导出 3:4。',
                  '主图文件上限未找到官方数值，按详情图的 2 MB 压缩；建议干净或白色背景，不得拼图或加水印。'],
        'sources': ['https://school.xiaohongshu.com/en/open/product/create-spu.html'],
    },
    {
        'id': 'shopify', 'name': 'Shopify 独立站', 'group': '跨境',
        'aspects': ['1:1', '3:4', '4:3'], 'aspect': '1:1',
        'min_px': 800, 'target_px': 2048, 'max_px': 5000, 'max_bytes': 20 * MB,
        'white': 'optional', 'fill_min': None, 'fill_target': .85,
        'confidence': 'official',
        'notes': ['推荐 2048×2048 方图，超过 800×800 才能缩放；单图最大 5000×5000（2500 万像素）、20 MB。',
                  '对背景和产品占比没有硬性规定，同一店铺保持统一画幅即可。'],
        'sources': ['https://help.shopify.com/en/manual/products/product-media/product-media-types'],
    },
    {
        'id': 'tiktok', 'name': 'TikTok Shop', 'group': '跨境',
        'aspects': ['1:1'], 'aspect': '1:1',
        'min_px': 800, 'target_px': 1600, 'max_px': 4000, 'max_bytes': 10 * MB,
        'white': 'required', 'fill_min': .6, 'fill_target': .85,
        'confidence': 'official',
        'notes': ['主图为方图，最低 600×600（英国站 800×800），10 MB 以内；这里按 800 px 作为最低要求。',
                  '美国站商品政策要求纯白底，其他站点为建议；部分站点要求产品占画面 60% 以上。',
                  '不得含文字、Logo、边框、水印或附加图形。'],
        'sources': ['https://seller-us.tiktok.com/university/essay?knowledge_id=3196690250417921',
                    'https://partner.tiktokshop.com/docv2/page/upload-product-image-202309'],
    },
    {
        'id': 'temu', 'name': 'Temu', 'group': '跨境',
        'aspects': ['1:1', '3:4'], 'aspect': '1:1',
        'min_px': 800, 'target_px': 1600, 'max_px': None, 'max_bytes': 3 * MB,
        'white': 'recommended', 'fill_min': None, 'fill_target': .85,
        'confidence': 'mixed',
        'notes': ['非服饰类目为 1:1、不低于 800×800；服饰类目为 3:4、不低于 1340×1785；单图 3 MB 以内。',
                  '建议白色或中性背景；未找到官方的产品占比要求。'],
        'sources': ['https://partner-eu.temu.com/documentation?menu_code=85762c6ccc5a4dbc8c023ea5e10c6dc0&sub_menu_code=894d49588571477da64a04a5d81b53a7'],
    },
    {
        'id': 'aliexpress', 'name': 'AliExpress 速卖通', 'group': '跨境',
        'aspects': ['1:1'], 'aspect': '1:1',
        'min_px': 800, 'target_px': 1000, 'max_px': None, 'max_bytes': 5 * MB,
        'white': 'recommended', 'fill_min': None, 'fill_target': .8,
        'white_slot': {'label': '白底图', 'aspect': '1:1', 'px': 800, 'max_bytes': 5 * MB,
                       'shadow': False, 'fill_target': .8},
        'confidence': 'official',
        'notes': ['商品图比例 1:1 至 1:1.3、不低于 800×800、建议 1000×1000、5 MB 以内；建议白底或纯色底，主体占 70% 以上。',
                  '白底图是独立槽位：纯白或透明底，主体居中并与边缘保持至少 50 px；不得加边框、水印、拼图或促销文字。'],
        'sources': ['https://sell.aliexpress.com/zh/__pc/post_image_127468002.htm'],
    },
    {
        'id': 'ebay', 'name': 'eBay', 'group': '跨境',
        'aspects': ['1:1', '4:3', '3:4'], 'aspect': '1:1',
        'min_px': 500, 'target_px': 1600, 'max_px': None, 'max_bytes': 12 * MB,
        'white': 'optional', 'fill_min': None, 'fill_target': .85,
        'confidence': 'official',
        'notes': ['最长边至少 500 px，建议 1600 px；单图 12 MB 以内；不限画幅与背景。',
                  '不得添加边框、文字、插图、营销素材或水印。'],
        'sources': ['https://www.ebay.com/help/selling/listings/adding-pictures-listings?id=4148',
                    'https://www.ebay.com/help/policies/listing-policies/picture-policy?id=4370'],
    },
]

_BY_ID = {platform['id']: platform for platform in PLATFORMS}
DEFAULT_PLATFORM = 'general'


def platform(ident):
    """Return a copy of one preset; unknown identifiers are a caller error."""
    if not isinstance(ident, str) or ident not in _BY_ID:
        raise ValueError('未知的销售平台：' + str(ident)[:40])
    return copy.deepcopy(_BY_ID[ident])


def catalog():
    return {'reviewed': REVIEWED, 'default': DEFAULT_PLATFORM, 'platforms': copy.deepcopy(PLATFORMS),
            'disclaimer': '数值整理自各平台公开的卖家规则与帮助文档（%s 检索）。平台会调整规则，上传前请以商家后台的提示为准。' % REVIEWED}


def recommended_margin(preset):
    """Plan margin that renders the product at the preset's target share."""
    return round(max(.03, min(.4, (1 - preset['fill_target']) / 2)), 3)


def plan_recommendation(preset, plan, new_project=False):
    """Plan fields worth changing so previews already match the marketplace.

    An existing project keeps any ratio the marketplace accepts; a new upload
    has no chosen ratio yet and starts from the marketplace's usual one.
    """
    patch = {}
    if plan.get('aspect') != preset['aspect'] and (new_project or plan.get('aspect') not in preset['aspects']):
        patch['aspect'] = preset['aspect']
    margin = recommended_margin(preset)
    if abs(plan.get('margin', .12) - margin) > .005:
        patch['margin'] = margin
    return patch

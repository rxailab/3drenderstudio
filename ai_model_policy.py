"""Explicit model choices, request compatibility and local spending restrictions.

This module never reads credentials, writes configuration or chooses a replacement
model. Prices are dated selection hints, not a live quote or connection result.
Provider schemas remain locally validated by ai_client; JSON mode is not a claim
that the upstream server enforces the complete photography-patch schema.
"""
import copy
import json
import re
import unicodedata
from urllib.parse import urlsplit


PRICE_CHECKED_AT = '2026-11-10'
BLOCKED_MODEL_MESSAGE = (
    '已按你的要求停用 GPT-5.5，未发送请求。请在 AI 设置中选择其他模型并保存；'
    '系统不会自动切换收费模型。'
)


def _price(currency, input_price, output_price, note, source):
    return {'currency': currency, 'per_tokens': 1000000,
            'input': input_price, 'output': output_price, 'note': note,
            'source': source, 'verified_at': PRICE_CHECKED_AT}


MODEL_PRESETS = (
    {'id': 'qwen-flash-cn', 'label': '千问 3.8 Flash · 低价',
     'provider': 'openai', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
     'model': 'qwen3.8-flash', 'key_label': '阿里云百炼 API Key', 'vision': True,
     'price': _price('CNY', .8, 2.7, '北京地域常规价，不含缓存折扣。',
                     'https://help.aliyun.com/zh/model-studio/qwen3-8-flash'),
     'note': '北京地域官方接口。支持图片；需填写对应地域的 Key 并测试连接。'},
    {'id': 'claude-haiku-official', 'label': 'Claude Haiku 5.5 · 低价',
     'provider': 'anthropic', 'base_url': 'https://api.anthropic.com',
     'model': 'claude-haiku-5-5', 'key_label': 'Anthropic API Key', 'vision': True,
     'price': _price('USD', .10, .50,
                     '提示不超过 10 万 token；超过时输入 $0.50、输出 $2.50 / 百万 token。',
                     'https://platform.claude.com/docs/en/models/haiku-5-5/overview'),
     'note': 'Anthropic 官方接口。支持图片；需单独填写 Key 并测试连接。'},
    {'id': 'deepseek-flash-official', 'label': 'DeepSeek Flash · 低价',
     'provider': 'openai', 'base_url': 'https://api.deepseek.com',
     'model': 'deepseek-flash', 'key_label': 'DeepSeek API Key', 'vision': True,
     'price': _price('USD', .15, .60,
                     '非高峰价；高峰输入 $0.30、输出 $1.20 / 百万 token。',
                     'https://api-docs.deepseek.com/quick_start/pricing/'),
     'note': '官方 Flash 支持图片。应用使用 JSON 模式并在本地验证参数；需测试连接。'},
    {'id': 'gpt-sol-official', 'label': 'GPT-6.1 Sol · 质量与费用兼顾',
     'provider': 'openai', 'base_url': 'https://api.openai.com/v1',
     'model': 'gpt-6.1-sol', 'key_label': 'OpenAI API Key', 'vision': True,
     'price': _price('USD', 2., 10., '输入不超过 27.2 万 token 的标准价；输出包括推理 token。',
                     'https://developers.openai.com/api/docs/models/gpt-6.1-sol'),
     'note': 'OpenAI 官方接口。支持图片与 JSON；本应用使用 Chat Completions，不调用远程工具。'},
    {'id': 'claude-sonnet-official', 'label': 'Claude Sonnet 5.5 · 复杂调整',
     'provider': 'anthropic', 'base_url': 'https://api.anthropic.com',
     'model': 'claude-sonnet-5-5', 'key_label': 'Anthropic API Key', 'vision': True,
     'price': _price('USD', 2., 10., '标准实时请求，不含缓存或批处理折扣。',
                     'https://platform.claude.com/docs/en/models/sonnet-5-5/overview'),
     'note': 'Anthropic 官方接口。支持图片；费用高于 Haiku，需自行选择并测试连接。'},
    {'id': 'gemini-flash-official', 'label': 'Gemini 3.8 Flash · 多图分析',
     'provider': 'openai', 'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai',
     'model': 'gemini-3.8-flash', 'key_label': 'Gemini API Key', 'vision': True,
     'price': _price('USD', .75, 3.75,
                     '截至 2026-12-31；2027-01-01 起输入 $1.50、输出 $7.50 / 百万 token。',
                     'https://ai.google.dev/gemini-api/docs/pricing/'),
     'note': 'Google 官方 OpenAI 兼容接口。支持图片；需单独填写 Key 并测试连接。'},
)


def model_presets():
    """Return display-only choices; selecting one still requires an explicit save."""
    return copy.deepcopy(list(MODEL_PRESETS))


def _model_name(value):
    return unicodedata.normalize('NFKC', value).strip().lower() if isinstance(value, str) else ''


def is_blocked_model(model):
    # Include dated snapshots, latest/pro aliases, and gateway namespaces. Do not
    # rely on a provider/host check: the user's restriction follows the model.
    return bool(re.search(r'(?<![a-z0-9])gpt[-_]5[._-]5(?:$|[^a-z0-9])', _model_name(model)))


def model_block_reason(model):
    return BLOCKED_MODEL_MESSAGE if is_blocked_model(model) else ''


def request_policy(config, require_vision=False):
    reason = model_block_reason(config.get('model'))
    code = 'blocked_model' if reason else ''
    if not reason and require_vision and _model_name(config.get('model')).split('/')[-1] == 'deepseek-v4-pro':
        reason = 'DeepSeek V4 Pro 不支持图片，请选择 DeepSeek Flash 或其他视觉模型。'
        code = 'unsupported_vision'
    return {'request_allowed': bool(config.get('enabled', True) and config.get('api_key')
                                    and config.get('model') and not reason),
            'blocked_reason': reason, 'blocked_code': code}


def assert_model_allowed(config, body=None):
    """Reject prohibited IDs before payload work and again immediately before IO."""
    if not isinstance(config, dict):
        raise ValueError('AI 配置格式无效。')
    if is_blocked_model(config.get('model')) or (isinstance(body, dict) and is_blocked_model(body.get('model'))):
        raise ValueError(BLOCKED_MODEL_MESSAGE)
    if config.get('enabled') is False:
        raise ValueError('AI 已关闭，未发送请求。请在 AI 设置中启用后重试。')
    if body is not None:
        if not isinstance(body, dict):
            raise ValueError('AI 请求格式无效。')
        if body.get('model') != config.get('model'):
            raise ValueError('AI 请求与保存的模型不一致，未发送请求。请重新保存设置后重试。')


def resolve_endpoint(config):
    """Preserve custom gateway paths while supporting official preset endpoints."""
    base = config['base_url'].rstrip('/')
    parsed = urlsplit(base)
    local_http = parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if ((parsed.scheme != 'https' and not local_http) or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('AI API 地址必须为不含账号、查询参数的 HTTPS 地址。')
    if config.get('provider') not in ('anthropic', 'openai'):
        raise ValueError('不支持的 AI 接口协议。')
    tail = '/messages' if config['provider'] == 'anthropic' else '/chat/completions'
    if base.endswith(tail):
        return base
    if config['provider'] == 'openai':
        if parsed.hostname == 'api.deepseek.com' and parsed.path in ('', '/beta'):
            return base + tail
        if parsed.hostname == 'generativelanguage.googleapis.com' and parsed.path.endswith('/openai'):
            return base + tail
    return base + ('' if base.endswith('/v1') else '/v1') + tail


def _append_system(body, text, provider):
    """Append deterministic contract text once, including when _post sanitizes again."""
    if provider == 'anthropic':
        system = body.get('system', '')
        if isinstance(system, str):
            if text not in system:
                body['system'] = system + '\n' + text
        elif isinstance(system, list) and not any(isinstance(p, dict) and p.get('text') == text for p in system):
            system.append({'type': 'text', 'text': text})
    else:
        messages = body.setdefault('messages', [])
        if not any(isinstance(m, dict) and m.get('role') == 'system' and
                   isinstance(m.get('content'), str) and text in m['content'] for m in messages):
            messages.insert(0, {'role': 'system', 'content': text})


def _has_image(value):
    if isinstance(value, dict):
        return value.get('type') in ('image', 'image_url', 'input_image') or any(_has_image(v) for v in value.values())
    return isinstance(value, list) and any(_has_image(v) for v in value)


def _check_deepseek_strict_schema(schema):
    """Never turn optional patch properties into mandatory mutations."""
    if not isinstance(schema, dict):
        raise ValueError('DeepSeek 严格工具模式需要有效的 JSON Schema。')
    if schema.get('type') == 'object':
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is not False or set(schema.get('required', [])) != set(props):
            raise ValueError('DeepSeek 严格工具模式要求所有字段必填；请使用 JSON 模式及本地校验处理局部调整。')
    for key, value in schema.items():
        if key == 'properties' and isinstance(value, dict):
            for child in value.values():
                _check_deepseek_strict_schema(child)
        elif key in ('items', 'additionalProperties') and isinstance(value, dict):
            _check_deepseek_strict_schema(value)
        elif key in ('anyOf', 'allOf', 'oneOf') and isinstance(value, list):
            for child in value:
                _check_deepseek_strict_schema(child)


def prepare_request_body(config, body):
    """Return an idempotent copy with provider-specific compatible parameters.

Do not change the endpoint/model or remove local value/identity validation. In
particular, ordinary JSON mode is deliberately kept for the optional patch
schema; adding strict:true to that schema would reject many valid requests.
"""
    assert_model_allowed(config, body)
    result = copy.deepcopy(body)
    model = _model_name(config.get('model')).split('/')[-1]
    provider = config.get('provider')
    if model == 'deepseek-v4-pro' and _has_image(result.get('messages')):
        raise ValueError('DeepSeek V4 Pro 不支持图片，请选择 DeepSeek Flash 或其他视觉模型。')
    if provider == 'anthropic' and re.match(r'^claude-(?:haiku|sonnet|opus)-5(?:[.-]|$)', model):
        for field in ('temperature', 'top_p', 'top_k'):
            result.pop(field, None)
        sonnet55 = bool(re.match(r'^claude-sonnet-5[.-]5(?:[.-]|$)', model))
        haiku55 = bool(re.match(r'^claude-haiku-5[.-]5(?:[.-]|$)', model))
        if sonnet55:
            choice = result.get('tool_choice', {})
            if isinstance(choice, dict) and choice.get('type') in ('any', 'tool'):
                result['tool_choice'] = {'type': 'auto'}
                _append_system(result, 'For this data-only request, return the requested result using '
                               'the supplied submission tool. Do not replace the structured result with prose.', provider)
            thinking = result.get('thinking', {})
            if isinstance(thinking, dict) and thinking.get('type') == 'disabled':
                result['thinking'] = {**thinking, 'type': 'between_tools'}
            if result.get('max_tokens', 8192) <= 256 and 'thinking' not in result:
                result['thinking'] = {'type': 'between_tools'}
        elif haiku55 and result.get('max_tokens', 8192) <= 256 and 'thinking' not in result:
            # The small connection probe must leave its token budget for "OK".
            result['thinking'] = {'type': 'disabled'}
    if provider == 'openai' and re.match(r'^gpt-6(?:[.-]|$)', model):
        if 'max_tokens' in result:
            result.setdefault('max_completion_tokens', result.pop('max_tokens'))
        result.setdefault('reasoning_effort', 'low')
        if (re.match(r'^gpt-(?:6\.1-sol|6-astra)(?:[.-]|$)', model)
                and result['reasoning_effort'] in ('none', 'minimal')):
            raise ValueError('此 GPT-6 模型不支持关闭推理或 minimal；请使用 low 或更高推理级别。')
        if result['reasoning_effort'] != 'none':
            for field in ('temperature', 'top_p', 'logprobs', 'top_logprobs'):
                result.pop(field, None)
        if re.match(r'^gpt-6(?:\.1-(?:sol|terra)|-astra)(?:[.-]|$)', model) and result.get('tools'):
            raise ValueError('此 GPT-6 模型的工具调用需要 Responses API；当前接口请使用 JSON 输出与本地校验。')
    if provider == 'openai' and model.startswith('deepseek-'):
        response_format = result.get('response_format', {})
        if isinstance(response_format, dict) and response_format.get('type') == 'json_schema':
            schema_config = response_format.get('json_schema')
            schema = schema_config.get('schema') if isinstance(schema_config, dict) else None
            if not isinstance(schema, dict):
                raise ValueError('AI 结构化输出格式无效。')
            result['response_format'] = {'type': 'json_object'}
            _append_system(result, 'Return only JSON matching this schema; all values will be validated locally: '
                           + json.dumps(schema, ensure_ascii=False, allow_nan=False), provider)
        for tool in result.get('tools', []):
            function = tool.get('function', {}) if isinstance(tool, dict) else {}
            if function.get('strict') is True:
                parsed = urlsplit(config['base_url'])
                if parsed.hostname != 'api.deepseek.com' or parsed.path.rstrip('/') not in ('/beta', '/beta/chat/completions'):
                    raise ValueError('DeepSeek 严格工具模式需要其 /beta 接口；请明确配置地址，或使用 JSON 模式及本地校验。')
                _check_deepseek_strict_schema(function.get('parameters'))
    return result

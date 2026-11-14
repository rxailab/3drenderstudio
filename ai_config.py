"""Local AI configuration. Secrets stay in .env, outside downloadable assets."""
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from studio_store import ROOT, DATA, atomic_json
from ai_model_policy import model_presets, request_policy

ENV_PATH = ROOT / '.env'
STATUS_PATH = DATA / 'ai_connection.json'
VISION_STATUS_PATH = DATA / 'ai_vision_connection.json'
CONFIG_LOCK = threading.RLock()
DEFAULT_BASE = 'https://api.hai.network/anthropic'
DEFAULT_MODEL = 'claude-sonnet-4-6'
ENV_KEYS = {'provider':'AI_PROVIDER', 'base_url':'AI_BASE_URL', 'model':'AI_MODEL',
            'api_key':'AI_API_KEY', 'enabled':'AI_ENABLED'}
VISION_KEYS = {'provider':'AI_VISION_PROVIDER','base_url':'AI_VISION_BASE_URL','model':'AI_VISION_MODEL',
               'api_key':'AI_VISION_API_KEY','enabled':'AI_VISION_ENABLED'}
# Image generation (OpenAI Images API). On HAI it follows the main account:
# same token, the gateway's OpenAI route, its current image model.
IMAGE_KEYS = {'base_url':'AI_IMAGE_BASE_URL','model':'AI_IMAGE_MODEL','api_key':'AI_IMAGE_API_KEY',
              'enabled':'AI_IMAGE_ENABLED','quality':'AI_IMAGE_QUALITY'}
HAI_IMAGE_BASE = 'https://api.hai.network/openai/v1'
DEFAULT_IMAGE_MODEL = 'gpt-image-2'
IMAGE_QUALITIES = ('low','medium','high')


def _official_hai(url):
    parsed=urlsplit(url)
    return (parsed.scheme=='https' and parsed.hostname=='api.hai.network' and parsed.port in (None,443)
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
            and bool(re.fullmatch(r'/(?:openai|anthropic|google)(?:/v1(?:/(?:chat/completions|messages))?)?/?',parsed.path)))


def read_env():
    values = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_config():
    with CONFIG_LOCK:
        values = read_env()
        def value(name, default=''):
            key = ENV_KEYS[name]
            return values.get(key, os.getenv(key, default))
        base = value('base_url', DEFAULT_BASE).rstrip('/')
        provider = value('provider') or ('anthropic' if '/anthropic' in base else 'openai')
        return {'provider':provider, 'base_url':base, 'model':value('model', DEFAULT_MODEL if provider=='anthropic' else ''),
                'api_key':value('api_key'), 'enabled':value('enabled', '1').lower() not in ('0','false','no','off')}


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def get_vision_config():
    """Independent vision endpoint; never forward a key across unrelated hosts."""
    with CONFIG_LOCK:
        main=get_config();values=read_env()
        fields={name:values.get(key,os.getenv(key,'')) for name,key in VISION_KEYS.items()}
        inherits=not any(fields.get(k) for k in ('provider','base_url','model','enabled'))
        if inherits: return {**main,'inherits':True}
        base=(fields['base_url'] or main['base_url']).rstrip('/')
        provider=fields['provider'] or (main['provider'] if base==main['base_url'] else ('anthropic' if '/anthropic' in base else 'openai'))
        model=fields['model'] or main['model']
        same_endpoint=(base,provider)==(main['base_url'],main['provider'])
        same_hai=_official_hai(base) and _official_hai(main['base_url'])
        key=fields['api_key'] or (main['api_key'] if same_endpoint or same_hai else '')
        enabled=fields['enabled'].lower() not in ('0','false','no','off') if fields['enabled'] else main['enabled']
        return {'provider':provider,'base_url':base,'model':model,'api_key':key,'enabled':enabled,'inherits':inherits}


def get_image_config():
    """Where usage scenes with people are generated. A key never follows to an unrelated host."""
    with CONFIG_LOCK:
        main=get_config();values=read_env()
        fields={name:values.get(key,os.getenv(key,'')).strip() for name,key in IMAGE_KEYS.items()}
        hai=_official_hai(main['base_url'])
        base=(fields['base_url'] or (HAI_IMAGE_BASE if hai else '')).rstrip('/')
        model=fields['model'] or (DEFAULT_IMAGE_MODEL if _official_hai(base) else '')
        same=bool(base) and (base==main['base_url'] or (hai and _official_hai(base)))
        key=fields['api_key'] or (main['api_key'] if same else '')
        enabled=fields['enabled'].lower() not in ('0','false','no','off') if fields['enabled'] else main['enabled']
        quality=fields['quality'].lower() if fields['quality'].lower() in IMAGE_QUALITIES else 'medium'
        return {'provider':'openai','base_url':base,'model':model,'api_key':key,'enabled':enabled,'quality':quality}


def public_image_config():
    config=get_image_config()
    visible={k:v for k,v in config.items() if k!='api_key'}
    visible['key_configured']=bool(config['api_key'])
    problem=''
    if config['base_url'] and config['model']:
        try: validate_config({**config,'api_key':config['api_key'] or 'x'})
        except ValueError as exc: problem=str(exc)
    if not config['enabled']: visible.update(status='disabled',message='图像生成已关闭')
    elif not config['base_url'] or not config['model']:
        visible.update(status='not_configured',message='未配置图像模型：在 .env 中填写 AI_IMAGE_BASE_URL 与 AI_IMAGE_MODEL，或使用 HAI 接口')
    elif problem: visible.update(status='error',message='图像模型配置无效：'+problem)
    elif not config['api_key']: visible.update(status='missing_key',message='图像接口缺少 API Key')
    else: visible.update(status='configured',message='图像模型 '+config['model']+' 已配置')
    visible['available']=visible['status']=='configured'
    return visible


def public_config():
    config = get_config()
    public = {k:v for k,v in config.items() if k != 'api_key'}
    public['key_configured'] = bool(config['api_key'])
    public.update(request_policy(config))
    if not config['enabled']:
        public.update(status='disabled', message='AI 已关闭，使用内置规则')
    elif public['blocked_reason']:
        # A legacy .env remains visible and editable. A previously successful
        # connection record must never reactivate a prohibited model.
        public.update(status=public['blocked_code'], message=public['blocked_reason'])
    elif not config['api_key']:
        label = 'HAI Access Token' if _official_hai(config['base_url']) else '该服务的 API Key'
        public.update(status='missing_key', message='服务地址已设置，待填写 '+label)
    elif not config['model']:
        public.update(status='configured', message='请填写模型 ID')
    else:
        public.update(status='configured', message='已配置，尚未通过真实连接测试')
        try:
            saved = json.loads(STATUS_PATH.read_text(encoding='utf-8'))
            if saved.get('fingerprint') == fingerprint(config):
                public.update(status='verified' if saved['success'] else 'error', message=saved['message'], last_test=saved['at'])
        except (OSError, ValueError, KeyError):
            pass
    vision=get_vision_config()
    visible={k:v for k,v in vision.items() if k!='api_key'}
    visible['key_configured']=bool(vision['api_key'])
    visible.update(request_policy(vision, require_vision=True))
    if not vision['enabled']: visible.update(status='disabled',message='视觉分析已关闭')
    elif visible['blocked_reason']: visible.update(status=visible['blocked_code'],message=visible['blocked_reason'])
    elif not vision['api_key']: visible.update(status='missing_key',message='视觉接口缺少 API Key')
    elif not vision['model']: visible.update(status='configured',message='请填写视觉模型 ID')
    else:
        visible.update(status='configured',message='视觉模型尚未通过真实图片测试')
        try:
            saved=json.loads(VISION_STATUS_PATH.read_text(encoding='utf-8'))
            if saved.get('fingerprint')==fingerprint(vision):
                visible.update(status='verified' if saved['success'] else 'error',message=saved['message'],last_test=saved['at'])
        except (OSError,ValueError,KeyError): pass
    public['vision_config']=visible
    public['image_config']=public_image_config()
    public['model_presets']=model_presets()
    return public


def validate_config(config):
    if config['provider'] not in ('anthropic','openai'):
        raise ValueError('接口格式应为 Anthropic 或 OpenAI 兼容')
    for name in ('base_url','model','api_key'):
        value = config[name]
        if not isinstance(value, str) or any(ord(c) < 32 for c in value):
            raise ValueError('配置中不能包含换行或控制字符')
    if len(config['api_key']) > 4096 or len(config['model']) > 200:
        raise ValueError('配置内容过长')
    url = urlsplit(config['base_url'])
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('请填写不含账号、查询参数的 API 基础地址')
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('localhost','127.0.0.1','::1')):
        raise ValueError('远程 API 必须使用 HTTPS')
    if not isinstance(config['enabled'], bool):
        raise ValueError('AI 开关无效')
    return config


def save_config(body):
    if not isinstance(body, dict):
        raise ValueError('配置格式无效')
    with CONFIG_LOCK:
        config = get_config()
        for field in ('provider','base_url','model','enabled'):
            if field in body:
                config[field] = body[field].strip() if isinstance(body[field],str) else body[field]
        if 'api_key' in body and not isinstance(body['api_key'], str):
            raise ValueError('API Key 格式无效')
        if body.get('api_key', '').strip():
            config['api_key'] = body['api_key'].strip()
        if body.get('clear_key') is True:
            config['api_key'] = ''
        config['base_url'] = config['base_url'].rstrip('/') if isinstance(config['base_url'],str) else config['base_url']
        validate_config(config)
        # A credential must not silently travel to a newly entered provider.
        old = get_config()
        if (config['base_url'],config['provider']) != (old['base_url'],old['provider']) and not body.get('api_key', '').strip():
            config['api_key'] = ''
        vision_old=get_vision_config()
        values=read_env()
        vision_fields={name:values.get(key,'') for name,key in VISION_KEYS.items()}
        for name in ('provider','base_url','model','enabled'):
            key='vision_'+name
            if key in body:
                value=body[key]
                if name=='enabled' and isinstance(value,bool): value='1' if value else '0'
                if not isinstance(value,str): raise ValueError('视觉配置格式无效')
                vision_fields[name]=value.strip().rstrip('/') if name=='base_url' else value.strip()
        if 'vision_api_key' in body and not isinstance(body['vision_api_key'],str): raise ValueError('视觉 API Key 格式无效')
        if body.get('vision_api_key','').strip(): vision_fields['api_key']=body['vision_api_key'].strip()
        if body.get('vision_clear_key') is True: vision_fields['api_key']=''
        newbase=vision_fields['base_url'] or config['base_url']
        newprovider=vision_fields['provider'] or (config['provider'] if newbase==config['base_url'] else ('anthropic' if '/anthropic' in newbase else 'openai'))
        active=any(vision_fields.get(k) for k in ('provider','base_url','model','enabled'))
        if active and vision_fields['api_key'] and (newbase,newprovider)!=(vision_old['base_url'],vision_old['provider']) and not body.get('vision_api_key','').strip():
            if not (_official_hai(vision_old['base_url']) and _official_hai(newbase)):
                vision_fields['api_key']=''
        validate_config({'provider':newprovider,'base_url':newbase,'model':vision_fields['model'] or config['model'],'api_key':vision_fields['api_key'],'enabled':True})
        if vision_fields['enabled'] not in ('','1','0','true','false','yes','no','on','off'): raise ValueError('视觉开关无效')
        lines = ENV_PATH.read_text(encoding='utf-8-sig').splitlines() if ENV_PATH.exists() else []
        names = set(ENV_KEYS.values()) | set(VISION_KEYS.values())
        lines = [line for line in lines if line.lstrip().startswith('#') or '=' not in line or line.split('=',1)[0].strip() not in names]
        for field, name in ENV_KEYS.items():
            value = ('1' if config[field] else '0') if field=='enabled' else config[field]
            lines.append(name+'='+value)
        for field,name in VISION_KEYS.items():
            if vision_fields[field]: lines.append(name+'='+vision_fields[field])
        temp = ENV_PATH.with_suffix('.env.tmp')
        temp.write_text('\n'.join(lines)+'\n', encoding='utf-8')
        os.replace(temp, ENV_PATH)
        return public_config()


def record_result(config, success, message):
    # Callers provide a safe message, with a final guard against accidental echo.
    message = str(message)[:500]
    if config.get('api_key'):
        message = message.replace(config['api_key'], '[redacted]')
    with CONFIG_LOCK:
        atomic_json(STATUS_PATH, {'fingerprint':fingerprint(config), 'success':bool(success), 'message':message, 'at':time.time()})


def record_vision_result(config, success, message):
    message=str(message)[:500]
    if config.get('api_key'): message=message.replace(config['api_key'],'[redacted]')
    with CONFIG_LOCK:
        atomic_json(VISION_STATUS_PATH,{'fingerprint':fingerprint(config),'success':bool(success),'message':message,'at':time.time()})

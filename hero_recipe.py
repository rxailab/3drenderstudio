"""Data-only studio recipes, bounded to measured scene capabilities.

Recipes can reorganize photography and tune measured shader inputs. They never
contain Python, file paths, new product geometry, color edits or generated logos.
"""
import copy
import math

RECIPE_VERSION = 1
CAMERA_LIMITS = {'occupancy':(.72,.92),'azimuth_delta':(-10,10),
                 'elevation_delta':(-10,10),'focal_length':(35,140)}
LIGHT_LIMITS = {'azimuth':(-180,180),'elevation':(5,85),'power':(.1,3),
                'width':(.35,3),'height':(.35,3)}
REFLECTION_LIMITS = {'power':(0,.12),'width':(.2,2),'height':(.15,2)}
BACKGROUND_LIMITS = {'contact_strength':(.08,.65),'contact_distance':(.02,.3)}
SURFACE_LIMITS = {'roughness':(.12,.9),'metallic':(0,1),'specular':(.15,.65),
                  'coat_weight':(0,.15),'coat_roughness':(.15,.7),'anisotropic':(0,.6)}


def _object(value,allowed,label):
    if not isinstance(value,dict) or set(value)-set(allowed):
        raise ValueError(label+' 包含无法执行的字段')
    return value


def _number(value,bounds,label):
    if type(value) not in (int,float) or not math.isfinite(value) or not bounds[0]<=value<=bounds[1]:
        raise ValueError(label+' 超出支持范围')
    return value


def _numbers(value,limits,label):
    _object(value,limits,label)
    return {key:_number(number,limits[key],label+'.'+key) for key,number in value.items()}


def _editable(material):
    value=material.get('editable',[])
    if isinstance(value,dict):return {key for key,allowed in value.items() if allowed is True}
    return set(value) if isinstance(value,list) and all(isinstance(key,str) for key in value) else set()


def normalize_recipe(raw,catalog,protections=()):
    """Return an executable recipe and plain reasons for independent omissions.

    Malformed data is rejected. Unsupported real targets and explicit protected
    domains are removed independently, so one uncertain material does not block
    valid lighting, framing and background work.
    """
    _object(raw,{'version','camera','lighting','background','surfaces'},'主图方案')
    if raw.get('version',RECIPE_VERSION)!=RECIPE_VERSION or type(raw.get('version',RECIPE_VERSION)) is not int:
        raise ValueError('主图方案版本无效')
    if not isinstance(catalog,dict):raise ValueError('真实场景目录无效')
    locks=set(protections or ())
    clean={'version':RECIPE_VERSION,'camera':{},'lighting':{},'background':{},'surfaces':[]}
    skipped=[]
    camera=_numbers(raw.get('camera',{}),CAMERA_LIMITS,'相机')
    if locks & {'preserve_camera','only_lighting','only_exposure','only_margin','shots','camera'}:
        if camera:skipped.append('按当前保护条件保留相机与构图。')
    else:
        clean['camera']=camera
        if locks & {'preserve_framing','margin','aspect'}:
            clean['camera'].pop('occupancy',None)

    lighting=_object(raw.get('lighting',{}),{'key','fill','rim','reflection','environment'},'布光')
    checked_lights={}
    for role,value in lighting.items():
        if role=='environment':checked_lights[role]=_number(value,(0,1),'环境照明')
        elif role=='reflection':
            _object(value,{'enabled',*REFLECTION_LIMITS},'反射光')
            if 'enabled' in value and type(value['enabled']) is not bool:raise ValueError('反射光开关无效')
            checked_lights[role]={**({'enabled':value['enabled']} if 'enabled' in value else {}),
                                  **_numbers({k:v for k,v in value.items() if k!='enabled'},REFLECTION_LIMITS,'反射光')}
        else:checked_lights[role]=_numbers(value,LIGHT_LIMITS,role+'灯')
    light_locked=bool(locks & {'preserve_lighting','preserve_light_direction','only_camera','only_margin','only_exposure','lighting','light_softness'}
                      or any(key.startswith('lighting.') for key in locks))
    if light_locked:
        if checked_lights:skipped.append('按当前保护条件保留现有布光，不重建受保护的光源。')
    else:clean['lighting']=checked_lights

    background=_object(raw.get('background',{}),{'clean_white',*BACKGROUND_LIMITS},'背景')
    if 'clean_white' in background and type(background['clean_white']) is not bool:raise ValueError('白底开关无效')
    checked_background={**({'clean_white':background['clean_white']} if 'clean_white' in background else {}),
                        **_numbers({k:v for k,v in background.items() if k!='clean_white'},BACKGROUND_LIMITS,'接触影')}
    background_locked=bool(locks & {'preserve_background','only_camera','only_lighting','only_exposure','only_margin','style','background'})
    if background_locked:
        if checked_background:skipped.append('按当前保护条件保留背景与地面。')
    else:
        clean['background']=checked_background
        ground=catalog.get('ground')
        if not isinstance(ground,dict) or ground.get('editable') is not True:
            removed=any(key in clean['background'] for key in BACKGROUND_LIMITS)
            for key in BACKGROUND_LIMITS:clean['background'].pop(key,None)
            if removed:skipped.append('地面对象没有可靠标识，保留原地面，继续执行其他摄影调整。')

    surfaces=raw.get('surfaces',[])
    if not isinstance(surfaces,list) or len(surfaces)>64:raise ValueError('表面调整列表无效')
    materials={item.get('id'):item for item in catalog.get('materials',[]) if isinstance(item,dict) and isinstance(item.get('id'),str)}
    seen=set()
    material_locked=bool(locks & {'preserve_material','preserve_materials','only_camera','only_lighting','only_exposure','only_margin','material_overrides','object_material_overrides'})
    for item in surfaces:
        _object(item,{'id','reason',*SURFACE_LIMITS},'表面调整')
        ident=item.get('id');reason=item.get('reason','')
        if not isinstance(ident,str) or not ident or len(ident)>80 or ident in seen:raise ValueError('表面目标标识缺失或重复')
        seen.add(ident)
        if not isinstance(reason,str) or len(reason)>600:raise ValueError('表面调整依据无效')
        values=_numbers({k:v for k,v in item.items() if k not in ('id','reason')},SURFACE_LIMITS,'表面')
        target=materials.get(ident)
        if material_locked:
            skipped.append('按当前保护条件保留表面 '+ident+'。');continue
        if not target or target.get('protected') is True:
            skipped.append('保留未确认或受保护的表面 '+ident+'，不改文字、图案或复杂节点。');continue
        allowed=_editable(target)
        omitted=set(values)-allowed
        if omitted:skipped.append('表面 '+ident+' 的连接节点或不支持通道保持原样：'+'、'.join(sorted(omitted))+'。')
        values={key:value for key,value in values.items() if key in allowed}
        if values:clean['surfaces'].append({'id':ident,'reason':reason,**values})
    return clean,list(dict.fromkeys(skipped))


def validate_recipe(raw,catalog,protections=()):
    return normalize_recipe(raw,catalog,protections)[0]


def default_recipe(catalog,protections=()):
    """A labelled photographic starting point; not a guess of product materials."""
    camera=catalog.get('camera') or {}
    lens=camera.get('focal_length',70)
    if type(lens) not in (int,float) or not math.isfinite(lens):lens=70
    elevation=camera.get('elevation',18)
    lower=-3 if type(elevation) in (int,float) and elevation>12 else 0
    raw={'version':RECIPE_VERSION,
         'camera':{'occupancy':.84,'azimuth_delta':0,'elevation_delta':lower,'focal_length':min(105,max(65,lens*1.15))},
         'lighting':{
             'key':{'azimuth':-45,'elevation':40,'power':1.35,'width':1.7,'height':2},
             'fill':{'azimuth':25,'elevation':30,'power':.75,'width':1.5,'height':1.5},
             'rim':{'azimuth':135,'elevation':55,'power':.5,'width':1.35,'height':1.1},
             'reflection':{'enabled':True,'power':.03,'width':.9,'height':.4},'environment':.14},
         'background':{'clean_white':True,'contact_strength':.28,'contact_distance':.12},
         'surfaces':[]}
    return validate_recipe(raw,catalog,protections)


def recipe_schema(catalog):
    def obj(properties,required=()):
        return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}
    def numbers(limits):return {key:{'type':'number','minimum':low,'maximum':high} for key,(low,high) in limits.items()}
    targets=[item['id'] for item in catalog.get('materials',[]) if isinstance(item,dict)
             and isinstance(item.get('id'),str) and item.get('protected') is not True and _editable(item)]
    target={'type':'string','maxLength':80}
    if targets:target['enum']=targets
    surface=obj({'id':target,'reason':{'type':'string','minLength':1,'maxLength':600},**numbers(SURFACE_LIMITS)},('id','reason'))
    return obj({'version':{'type':'integer','enum':[RECIPE_VERSION]},'camera':obj(numbers(CAMERA_LIMITS)),
                'lighting':obj({**{role:obj(numbers(LIGHT_LIMITS)) for role in ('key','fill','rim')},
                                'reflection':obj({'enabled':{'type':'boolean'},**numbers(REFLECTION_LIMITS)}),
                                'environment':{'type':'number','minimum':0,'maximum':1}}),
                'background':obj({'clean_white':{'type':'boolean'},**numbers(BACKGROUND_LIMITS)}),
                'surfaces':{'type':'array','maxItems':64 if targets else 0,'items':surface}},
               ('version','camera','lighting','background','surfaces'))

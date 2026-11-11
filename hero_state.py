"""Compare measured native scenes with explicit, bounded float tolerances.

The digest remains an exact record fingerprint. Equivalence is a separate
operation: it never weakens product geometry, transform, color, label or
resource checks, and it reports the concrete field behind every mismatch.
"""
import hashlib
import json
import math
import re

SCENE_STATE_VERSION=2
PROTOCOL='hero-scene-state-v2'
PRODUCT_FIELDS=('geometry_sha256','transforms_sha256','color_sha256','resources_sha256','labels_sha256')
STATE_FIELDS={'version','product_scale','camera','canvas_aspect','lights','world_nodes','ground','ground_nodes',
              'surfaces','product','exposure','view_transform','look','gamma','unit_scale','film_transparent'}
CAMERA_FIELDS={'name','type','focal_length','shift_x','shift_y','ortho_scale','matrix_world','constraints',
               'sensor_fit','sensor_width','sensor_height','clip_start','clip_end','dof_enabled','focus_distance',
               'aperture_fstop','aperture_blades','aperture_ratio','aperture_rotation','focus_object'}
LIGHT_FIELDS={'name','role','type','visible','energy','color','size','size_y','shape','diffuse_factor',
              'matrix_world','use_nodes','nodes'}


def state_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _validate(catalog):
    if not isinstance(catalog,dict):return None,'invalid_catalog'
    state=catalog.get('scene_state')
    if not isinstance(state,dict) or state.get('version')!=SCENE_STATE_VERSION:return None,'missing_scene_state'
    if set(state)!=STATE_FIELDS:return None,'invalid_scene_state_schema'
    product=state.get('product')
    if not isinstance(product,dict) or set(product)!=set(PRODUCT_FIELDS):return None,'invalid_product_proof'
    if any(not isinstance(product[key],str) or not re.fullmatch('[a-f0-9]{64}',product[key]) for key in PRODUCT_FIELDS):
        return None,'invalid_product_proof'
    scale=state.get('product_scale')
    if type(scale) not in (int,float) or not math.isfinite(scale) or not 0<scale<1e9:return None,'invalid_product_scale'
    try:actual=state_digest(state)
    except (ValueError,TypeError):return None,'invalid_scene_state_value'
    if catalog.get('scene_state_sha256')!=actual:return None,'scene_state_checksum_mismatch'
    # Match the independently reported preservation fields as well.
    if any(catalog.get(key)!=product[key] for key in PRODUCT_FIELDS):return None,'scene_state_product_mismatch'
    if not isinstance(state.get('camera'),dict) or set(state['camera'])!=CAMERA_FIELDS:return None,'invalid_scene_state_schema'
    canvas=state.get('canvas_aspect')
    if not isinstance(canvas,list) or len(canvas)!=4 or any(type(v) not in (int,float) or v<=0 for v in canvas):return None,'invalid_scene_state_schema'
    if not isinstance(state.get('lights'),list) or any(not isinstance(row,dict) or not LIGHT_FIELDS<=set(row) for row in state['lights']):return None,'invalid_scene_state_schema'
    if not isinstance(state.get('surfaces'),list):return None,'invalid_scene_state_schema'
    return state,None


def _tolerance(path,a,b,scale):
    """Only allow Blender-sized serialization/projection roundoff, not edits."""
    # Product-relative absolute tolerance, capped in scene units. Never scale
    # tolerance by a large world-coordinate translation from an imported CAD.
    unit=min(1e-4,max(scale,1e-6)*2e-6)
    if path.startswith('$.product.') or path in ('$.product_scale','$.version','$.unit_scale'):return 0.0
    if path in ('$.exposure','$.gamma'):return 1e-6
    if path.startswith('$.canvas_aspect['):return 1e-8
    if path.startswith('$.camera.'):
        if '.matrix_world[' in path:
            return unit if re.search(r'\.matrix_world\[[012]\]\[3\]$',path) else 2e-6
        if path.endswith(('.focal_length','.sensor_width','.sensor_height')):return 1e-4
        if path.endswith(('.ortho_scale','.clip_start','.clip_end','.focus_distance')):return unit
        if path.endswith(('.shift_x','.shift_y','.aperture_fstop','.aperture_ratio','.aperture_rotation')):return 2e-6
        return 0.0
    if path.startswith('$.lights['):
        if '.matrix_world[' in path:
            return unit if re.search(r'\.matrix_world\[[012]\]\[3\]$',path) else 2e-6
        if path.endswith(('.size','.size_y','.shadow_soft_size')):return unit
        if path.endswith('.energy'):return max(1e-5,max(abs(a),abs(b))*2e-6)
        if '.color[' in path or path.endswith(('.diffuse_factor','.specular_factor','.volume_factor','.spread','.angle')):return 2e-6
        if '.nodes.' in path:return max(1e-7,min(2e-5,max(abs(a),abs(b))*2e-7))
        return 0.0
    if path.startswith('$.ground.'):
        if '.matrix_world[' in path:
            return unit if re.search(r'\.matrix_world\[[012]\]\[3\]$',path) else 2e-6
        return 0.0
    if path.startswith(('$.world_nodes.','$.ground_nodes[')):
        return max(1e-7,min(2e-5,max(abs(a),abs(b))*2e-7))
    if path.startswith('$.surfaces['):
        if path.endswith(('.roughness','.metallic','.specular','.coat_weight','.coat_roughness','.anisotropic','.transmission')):return 1e-6
        # Base colors are already protected by exact per-object hashes.
        return 0.0
    return 0.0


def compare_scene_states(left_catalog,right_catalog):
    """Return equivalence plus field-level diagnostics for two fresh catalogs.

    Legacy results intentionally cannot establish equality. Re-inspect both
    saved .blend files with the current executor before calling this function.
    """
    result={'protocol':PROTOCOL,'equivalent':False,'exact_match':False,'roundoff_only':False,
            'differences':[],'roundoff_fields':[],'difference_count':0,'roundoff_count':0}
    left,error=_validate(left_catalog)
    if error:return {**result,'reason':error,'invalid_side':'candidate'}
    right,error=_validate(right_catalog)
    if error:return {**result,'reason':error,'invalid_side':'final'}
    scale=min(left['product_scale'],right['product_scale'])
    def difference(path,a,b,kind,tolerance=None):
        result['difference_count']+=1
        if len(result['differences'])<32:
            row={'path':path,'before':a,'after':b,'kind':kind}
            if tolerance is not None:row['tolerance']=tolerance
            result['differences'].append(row)
    def walk(a,b,path):
        if type(a) is not type(b):
            # JSON numeric 1 versus 1.0 is the same physical scalar. Booleans
            # and integer counters remain distinct from numbers of other kind.
            if type(a) not in (int,float) or type(b) not in (int,float):
                difference(path,a,b,'type');return
        if isinstance(a,dict) and isinstance(b,dict):
            for key in sorted(set(a)|set(b)):
                if key not in a or key not in b:difference(path+'.'+key,a.get(key),b.get(key),'missing_field')
                else:walk(a[key],b[key],path+'.'+key)
            return
        if isinstance(a,(list,tuple)) and isinstance(b,(list,tuple)):
            if len(a)!=len(b):difference(path,{'length':len(a)},{'length':len(b)},'length');return
            for index,(old,new) in enumerate(zip(a,b)):walk(old,new,path+'['+str(index)+']')
            return
        if type(a) in (int,float) and type(b) in (int,float):
            if not math.isfinite(a) or not math.isfinite(b):difference(path,a,b,'non_finite');return
            if a==b:return
            tolerance=_tolerance(path,a,b,scale)
            if abs(a-b)<=tolerance:
                result['roundoff_count']+=1
                if len(result['roundoff_fields'])<32:result['roundoff_fields'].append({'path':path,'delta':b-a,'tolerance':tolerance})
            else:difference(path,a,b,'value',tolerance)
            return
        if a!=b:difference(path,a,b,'value')
    walk(left,right,'$')
    equivalent=result['difference_count']==0
    return {**result,'equivalent':equivalent,'exact_match':equivalent and left_catalog['scene_state_sha256']==right_catalog['scene_state_sha256'],
            'roundoff_only':equivalent and result['roundoff_count']>0,
            'reason':'equivalent_with_roundoff' if equivalent and result['roundoff_count'] else 'identical_scene' if equivalent else 'scene_fields_changed'}


def difference_summary(result,limit=4):
    if result.get('equivalent'):return '实际场景一致。'
    def label(path):
        names={'$.camera.shift_x':'相机水平偏移','$.camera.shift_y':'相机垂直偏移','$.camera.focal_length':'焦距',
               '$.exposure':'曝光','$.gamma':'色彩伽马','$.film_transparent':'背景透明设置',
               '$.product.geometry_sha256':'产品几何','$.product.transforms_sha256':'产品位置',
               '$.product.color_sha256':'产品颜色','$.product.resources_sha256':'贴图与资源',
               '$.product.labels_sha256':'标识与文字'}
        if path in names:return names[path]
        if path.startswith('$.camera.matrix_world'):return '相机位置或方向'
        if path.startswith('$.camera.'):return '相机设置'
        if path.startswith('$.lights['):
            if path.endswith('.energy'):return '灯光功率'
            if '.matrix_world' in path:return '灯光位置或方向'
            if path.endswith(('.size','.size_y')):return '柔光面积'
            return '灯光设置'
        if path.startswith('$.surfaces['):return '材质表面参数'
        if path.startswith('$.canvas_aspect'):return '画幅比例'
        if path.startswith('$.world_nodes'):return '环境照明'
        if path.startswith(('$.ground.','$.ground_nodes')):return '地面与接触影'
        if path in ('$.look','$.view_transform'):return '色彩管理'
        return '场景设置'
    fields=list(dict.fromkeys(label(row.get('path','')) for row in result.get('differences',[])[:limit]))
    if fields:return '实际场景参数不同：'+'、'.join(fields)
    reasons={'missing_scene_state':'旧结果需要重新读取工程核验。',
             'scene_state_checksum_mismatch':'场景记录与校验信息不一致。',
             'scene_state_product_mismatch':'产品保护记录与实际场景不一致。'}
    return reasons.get(result.get('reason'),'场景记录不完整，无法确认两个场景一致。')

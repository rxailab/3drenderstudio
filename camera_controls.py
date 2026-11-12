"""Shared camera data contract for live views and final renders."""
import copy
import hashlib
import math
from pathlib import Path

PROJECTIONS=('orthographic','perspective')
CAMERA_FIELDS=('azimuth','elevation','focal_length','projection','ortho_scale','camera_distance','target_offset','target_object')
ORTHO_SCALE_LIMITS=(.000001,1e9)
TARGET_OFFSET_LIMITS=(-10,10)
CONTRACT_VERSION=1


def projection(shot):
    value=shot.get('projection','orthographic')
    if value not in PROJECTIONS:raise ValueError('镜头投影方式无效')
    return value


def _number(value,low,high,label):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(label+' 必须为支持范围内的有限数值')
    return value


def validate_shot(shot):
    projection(shot)
    for key,limits in (('azimuth',(-360,360)),('elevation',(-80,90)),('focal_length',(20,200))):
        if key in shot:_number(shot[key],*limits,key)
    if 'ortho_scale' in shot:_number(shot['ortho_scale'],*ORTHO_SCALE_LIMITS,'正交画面宽度')
    if 'camera_distance' in shot:_number(shot['camera_distance'],*ORTHO_SCALE_LIMITS,'镜头距离')
    if 'target_offset' in shot:
        value=shot['target_offset']
        if not isinstance(value,list) or len(value)!=3:raise ValueError('镜头平移需要三个坐标')
        for coordinate in value:_number(coordinate,*TARGET_OFFSET_LIMITS,'镜头平移')
    return shot


def shot_request(shot):
    validate_shot(shot)
    return {'id':shot.get('id'),'kind':shot.get('kind','full'),**{key:copy.deepcopy(shot[key]) for key in CAMERA_FIELDS if key in shot},
            'projection':projection(shot)}


def file_digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def validate_report(report,contract,out):
    """Accept only a camera measured at render and again in the saved file."""
    if (not isinstance(report,dict) or report.get('contract')!=contract
            or report.get('render_completed') is not True or report.get('saved_scene_verified') is not True):
        raise RuntimeError('镜头投影尚未完成真实出图与保存核验')
    actual=report.get('actual',{})
    expected='ORTHO' if contract['shot']['projection']=='orthographic' else 'PERSP'
    if actual.get('type')!=expected:raise RuntimeError('实际镜头投影与本次要求不一致')
    if expected=='ORTHO' and actual.get('sensor_fit')!='HORIZONTAL':
        raise RuntimeError('正交镜头未使用一致的横向取景尺度')
    matrix=actual.get('matrix_world')
    if (not isinstance(matrix,list) or len(matrix)!=4 or any(not isinstance(row,list) or len(row)!=4
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in row) for row in matrix)):
        raise RuntimeError('实际镜头缺少有效变换矩阵')
    out=Path(out).resolve()
    for kind,suffix in (('png','.png'),('blend','.blend')):
        path=Path(report.get(kind+'_path','')).resolve()
        if path.parent!=out or path.suffix.lower()!=suffix or not path.is_file() or file_digest(path)!=report.get(kind+'_sha256'):
            raise RuntimeError('镜头投影核验与实际输出文件不一致')
    return report


def matches_result(result,shot):
    desired='ORTHO' if projection(shot)=='orthographic' else 'PERSP'
    camera=result.get('catalog',{}).get('camera',{})
    if result.get('proof',{}).get('saved_scene_verified') is True:
        return camera.get('type')==desired and (desired!='ORTHO' or camera.get('sensor_fit')=='HORIZONTAL')
    proof=result.get('camera_projection_verification',{})
    return (proof.get('saved_scene_verified') is True and proof.get('render_completed') is True
            and proof.get('contract',{}).get('shot')==shot_request(shot)
            and proof.get('actual',{}).get('type')==desired)

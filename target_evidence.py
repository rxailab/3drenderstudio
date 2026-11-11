"""Bounded, snapshot-specific surface evidence for natural-language part edits.

Ray samples establish pixel -> object/material identity.  They do not establish
the names of hidden parts, complete object coverage, or new geometry.
"""
import copy
import hashlib
import json
import math
import re

from intelligence_rules import InstructionOutcome


MAX_TARGETS = 80
MAX_SAMPLES = 3
MAX_RESULT_BYTES = 8 * 1024 * 1024
SOURCE_KEYS = ('output_id', 'shot_id', 'quality', 'version')
MAX_CATALOG_TARGETS = 160
MAX_COMPONENTS = 4096


class SemanticEvidenceError(ValueError):
    """A structural target/evidence failure in the LLM-owned intent path."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _pairs(analysis):
    result = set()
    for obj in (analysis or {}).get('objects', []):
        if not isinstance(obj, dict) or not isinstance(obj.get('name'), str):
            continue
        materials = obj.get('materials', [])
        if obj.get('type') == 'MESH' and not materials:
            materials = ['Default material']
        for material in materials if isinstance(materials, list) else []:
            if isinstance(material, str) and material:
                result.add((obj['name'], material))
    return result


def _point(value):
    return (isinstance(value, dict) and all(type(value.get(key)) in (int, float)
            and math.isfinite(value[key]) and 0 <= value[key] <= 1 for key in ('x', 'y')))


def _snapshot(value):
    return (isinstance(value, dict) and set(value) == set(SOURCE_KEYS)
            and all(isinstance(value.get(key), str) and 0 < len(value[key]) <= 128
                    for key in ('output_id', 'shot_id'))
            and value.get('quality') in ('preview', 'standard', 'fine', 'hero')
            and type(value.get('version')) is int and value['version'] >= 1)


def _result_matches_source(result, source):
    """Older workers omit identity fields; explicit identities must agree."""
    for container in (result, result.get('snapshot', {})):
        if not isinstance(container, dict):
            return False
        for key, expected in (('version', source['version']), ('shot_id', source['shot_id']),
                              ('quality', source['quality']), ('job_id', source['output_id']),
                              ('output_id', source['output_id'])):
            if key in container and (type(container[key]) is not type(expected) or container[key] != expected):
                return False
    shot = result.get('shot')
    if isinstance(shot, dict) and 'id' in shot and shot['id'] != source['shot_id']:
        return False
    return True


def _structure(obj):
    result = {}
    if isinstance(obj.get('parent'), str):
        result['parent'] = obj['parent'][:300]
    dimensions = obj.get('dimensions')
    if (isinstance(dimensions, list) and len(dimensions) == 3
            and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in dimensions)):
        result['dimensions'] = list(dimensions)
    return result


def public_evidence(value, analysis, version=None):
    """Strip file metadata and reject malformed or stale service-selected data."""
    if isinstance(value, dict) and value.get('method') == 'scene_part_catalog':
        return _public_catalog(value, analysis, version)
    if not isinstance(value, dict) or value.get('method') != 'camera_ray_samples':
        return {}
    source = value.get('source_snapshot')
    if not _snapshot(source) or (version is not None and source['version'] != version):
        return {}
    rows = value.get('targets')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_TARGETS:
        return {}
    known = _pairs(analysis); ids = set(); pairs = set(); targets = []
    for row in rows:
        if not isinstance(row, dict):
            return {}
        ident = row.get('id'); pair = (row.get('object_name'), row.get('material_name'))
        points = row.get('samples')
        if (not isinstance(ident, str) or not re.fullmatch(r't[1-9][0-9]{0,3}', ident)
                or ident in ids or not all(isinstance(v, str) for v in pair) or pair not in known
                or pair in pairs or not isinstance(points, list) or not 1 <= len(points) <= MAX_SAMPLES
                or not all(_point(point) for point in points)):
            return {}
        ids.add(ident); pairs.add(pair)
        target = {'id': ident, 'object_name': pair[0], 'material_name': pair[1],
                  'samples': [{key: point[key] for key in ('x', 'y')} for point in points]}
        target.update(_structure(row))
        targets.append(target)
    return {'method': 'camera_ray_samples', 'source_snapshot': copy.deepcopy(source),
            'coverage': 'visible_surfaces_only', 'truncated': bool(value.get('truncated')),
            'targets': targets}


def _bounds(value):
    return (isinstance(value, dict) and set(value) == {'min', 'max'}
            and all(isinstance(value.get(key), list) and len(value[key]) == 3
                    and all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e12
                            for v in value[key]) for key in ('min', 'max'))
            and all(value['min'][axis] <= value['max'][axis] for axis in range(3)))


def _inside(inner, outer):
    tolerance = max(1e-9, max(outer['max'][i] - outer['min'][i] for i in range(3)) * 1e-6)
    return all(outer['min'][i] - tolerance <= inner['min'][i]
               and inner['max'][i] <= outer['max'][i] + tolerance for i in range(3))


def _public_catalog(value, analysis, version):
    """The service supplies actual isolated geometry, never AI-created masks."""
    source = value.get('source_snapshot')
    rows = value.get('targets'); pages = value.get('catalog_pages')
    if (not _snapshot(source) or (version is not None and source['version'] != version)
            or value.get('generation_version') != 1
            or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CATALOG_TARGETS
            or not isinstance(pages, list) or not 1 <= len(pages) <= 4):
        return {}
    public_pages = []
    for index, page in enumerate(pages):
        if (not isinstance(page, dict) or page.get('label') != 'PART_CATALOG_' + str(index + 1)
                or any(type(page.get(key)) is not int or not 64 <= page[key] <= 1600
                       for key in ('width', 'height'))):
            return {}
        public_pages.append({key: page[key] for key in ('label', 'width', 'height')})
    known = _pairs(analysis); ids = set(); pairs = set(); targets = []
    for row in rows:
        if not isinstance(row, dict): return {}
        ident = row.get('id'); pair = (row.get('object_name'), row.get('material_name'))
        bounds = row.get('bounds'); components = row.get('components'); thumbnail = row.get('thumbnail')
        if (not isinstance(ident, str) or not re.fullmatch(r't[1-9][0-9]{0,3}', ident)
                or ident in ids or not all(isinstance(v, str) for v in pair) or pair not in known or pair in pairs
                or not _bounds(bounds) or not isinstance(components, list) or not 1 <= len(components) <= MAX_COMPONENTS
                or not isinstance(thumbnail, dict) or type(thumbnail.get('page')) is not int
                or not 0 <= thumbnail['page'] < len(pages)):
            return {}
        rect = thumbnail.get('rect')
        if (not isinstance(rect, list) or len(rect) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in rect)
                or min(rect[2:]) <= 0 or rect[0] + rect[2] > 1.000001 or rect[1] + rect[3] > 1.000001):
            return {}
        for component in components:
            if (not isinstance(component, dict) or not _bounds(component.get('bounds'))
                    or not _inside(component['bounds'], bounds)
                    or type(component.get('triangle_count')) is not int or component['triangle_count'] < 1):
                return {}
        digest = row.get('geometry_digest')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest): return {}
        ids.add(ident); pairs.add(pair)
        target = {'id': ident, 'object_name': pair[0], 'material_name': pair[1],
                        'bounds': copy.deepcopy(bounds), 'components': copy.deepcopy(components),
                        'geometry_digest': digest,
                        'thumbnail': {'page': thumbnail['page'], 'rect': list(rect)}}
        if 'surface_values' in row or 'linked_surface_fields' in row:
            values = row.get('surface_values', {}); linked = row.get('linked_surface_fields', [])
            fields = {'roughness', 'metallic', 'transmission'}
            if (not isinstance(values, dict) or set(values) - fields
                    or any(type(number) not in (int, float) or not math.isfinite(number)
                           or not 0 <= number <= 1 for number in values.values())
                    or not isinstance(linked, list) or len(linked) > len(fields)
                    or any(not isinstance(field, str) or field not in fields for field in linked)
                    or len(set(linked)) != len(linked) or set(values) & set(linked)):
                return {}
            target.update(surface_values=copy.deepcopy(values), linked_surface_fields=list(linked))
        targets.append(target)
    return {'method': 'scene_part_catalog', 'generation_version': 1,
            'source_snapshot': copy.deepcopy(source), 'coverage': 'inspected_material_surfaces',
            'geometry_only': True, 'truncated': bool(value.get('truncated')),
            'unresolved_count': min(10000, max(0, value.get('unresolved_count', 0)))
            if type(value.get('unresolved_count', 0)) is int else 0,
            'catalog_pages': public_pages, 'targets': targets}


def spatial_instance_count(rows):
    """Count separated groups of inspected geometry, not imported mesh names.

    All connected components of every edited material slot participate. This
    covers a tire split across meshes and several tires joined in one mesh.
    Bounding boxes are conservative: overlapping unrelated surfaces cannot be
    claimed as separate physical instances merely by renaming their meshes.
    """
    boxes = [component['bounds'] for row in rows for component in row.get('components', [])]
    if not boxes or len(boxes) > 8192:
        raise InstructionOutcome('needs_clarification', '部件的空间结构尚不能完整核验，当前方案未修改。')
    # A tolerance based on each component avoids bridging distinct parts when
    # the overall product has a large span or uses millimetres as model units.
    sizes = [math.sqrt(sum((box['max'][i] - box['min'][i]) ** 2 for i in range(3))) for box in boxes]
    positive = sorted(size for size in sizes if size > 0)
    tolerance = max(1e-9, (positive[len(positive)//2] if positive else 0) * 1e-4)
    parents = list(range(len(boxes)))
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]; index = parents[index]
        return index
    order = sorted(range(len(boxes)), key=lambda index: boxes[index]['min'][0])
    for offset, left in enumerate(order):
        for right in order[offset+1:]:
            if boxes[right]['min'][0] > boxes[left]['max'][0] + tolerance: break
            gaps = [max(0, boxes[left]['min'][i] - boxes[right]['max'][i],
                        boxes[right]['min'][i] - boxes[left]['max'][i]) for i in range(3)]
            if sum(gap * gap for gap in gaps) <= tolerance * tolerance:
                parents[root(right)] = root(left)
    return len({root(index) for index in range(len(boxes))})


def bound_instance_count(bindings, phrase, entries=None):
    """Only a server-resolved catalog binding may replace the mesh count."""
    related = [binding for binding in bindings or [] if binding.get('purpose') == 'material'
               and (binding.get('phrase', '') in phrase or phrase in binding.get('phrase', ''))]
    catalog = [binding for binding in related if binding.get('instance_basis') == 'scene_geometry_components'
               and type(binding.get('instance_count')) is int]
    if len(catalog) == 1 and len(related) == 1:
        if entries is not None:
            edited = {(entry.get('object_name'), entry.get('material_name')) for entry in entries if len(entry) > 2}
            required = {(row['object_name'], row['material_name']) for row in catalog[0]['targets']}
            if not required <= edited: return 0
        return catalog[0]['instance_count']
    return len({entry['object_name'] for entry in entries or [] if 'object_name' in entry and len(entry) > 2})


def model_evidence(value):
    """Keep full geometry for validation; send a bounded visual index to AI."""
    result = copy.deepcopy(value)
    if result.get('method') != 'scene_part_catalog': return result
    for row in result['targets']:
        row.pop('geometry_digest', None)
        components = row.pop('components')
        row['component_count'] = len(components)
        if len(components) <= 16:
            row['component_bounds'] = [component['bounds'] for component in components]
        else:
            row['component_bounds_omitted'] = True
    return result


def load_evidence(data_root, project, output):
    """Read only the trusted output record's current, project-confined result."""
    if not isinstance(output, dict) or output.get('version') != project.get('version'):
        return {}
    source = {'output_id': output.get('job_id'), **{k: output.get(k) for k in ('shot_id', 'quality', 'version')}}
    if not _snapshot(source):
        return {}
    url = output.get('result_url')
    if not isinstance(url, str) or not url.startswith('/files/'):
        return {}
    try:
        path = (data_root / url[7:]).resolve()
        if ((data_root / project['id']).resolve() not in path.parents or path.suffix.lower() != '.json'
                or not path.is_file() or path.stat().st_size > MAX_RESULT_BYTES):
            return {}
        raw = path.read_bytes()
        if len(raw) > MAX_RESULT_BYTES:
            return {}
        result = json.loads(raw)
    except (OSError, ValueError, UnicodeError, TypeError):
        return {}
    if not isinstance(result, dict) or result.get('success') is not True or not _result_matches_source(result, source):
        return {}
    samples = result.get('product_samples')
    if not isinstance(samples, list):
        return {}
    known = _pairs(project.get('analysis')); grouped = {}
    for sample in samples[:50000]:
        if not _point(sample):
            continue
        pair = (sample.get('object_name'), sample.get('material_name'))
        if not all(isinstance(v, str) for v in pair) or pair not in known:
            continue
        points = grouped.setdefault(pair, [])
        point = {key: sample[key] for key in ('x', 'y')}
        if point not in points and len(points) < 64:
            points.append(point)
    objects = {obj['name']: obj for obj in project.get('analysis', {}).get('objects', [])
               if isinstance(obj, dict) and isinstance(obj.get('name'), str)}
    targets = []
    for pair, points in sorted(grouped.items())[:MAX_TARGETS]:
        # A real sampled medoid, followed by separated real samples; never invent
        # an average pixel which may fall on another part or the background.
        center = {key: sum(point[key] for point in points) / len(points) for key in ('x', 'y')}
        selected = [min(points, key=lambda point: sum((point[k] - center[k]) ** 2 for k in ('x', 'y')))]
        while len(selected) < min(MAX_SAMPLES, len(points)):
            remaining = [point for point in points if point not in selected]
            selected.append(max(remaining, key=lambda point: min(sum((point[k] - chosen[k]) ** 2 for k in ('x', 'y')) for chosen in selected)))
        target = {'id': 't' + str(len(targets) + 1), 'object_name': pair[0], 'material_name': pair[1], 'samples': selected}
        target.update(_structure(objects.get(pair[0], {})))
        targets.append(target)
    evidence = public_evidence({'method': 'camera_ray_samples', 'source_snapshot': source, 'targets': targets,
                                'truncated': len(grouped) > MAX_TARGETS}, project.get('analysis'), project['version'])
    if evidence:
        evidence['result_digest'] = hashlib.sha256(raw).hexdigest()
    return evidence


def binding_schema(evidence):
    maximum = MAX_CATALOG_TARGETS if evidence.get('method') == 'scene_part_catalog' else MAX_TARGETS
    return {'type': 'array', 'maxItems': 20, 'items': {'type': 'object', 'additionalProperties': False,
            'properties': {'phrase': {'type': 'string', 'minLength': 1, 'maxLength': 160},
                           'purpose': {'type': 'string', 'enum': ['material', 'camera']},
                           'target_ids': {'type': 'array', 'minItems': 1, 'maxItems': maximum,
                                          'items': {'type': 'string', 'enum': [row['id'] for row in evidence['targets']]}}},
            'required': ['phrase', 'purpose', 'target_ids']}}


def _number(text):
    if text.isascii() and text.isdigit():
        return int(text)
    digits = dict(zip('零一二三四五六七八九', range(10))); digits['两'] = 2
    total = digit = 0
    for char in text:
        if char in digits:
            digit = digits[char]
        elif char in ('十', '百'):
            total += (digit or 1) * (10 if char == '十' else 100); digit = 0
        else:
            return None
    return total + digit


def _requested_count(text, phrase):
    number = r'(\d{1,3}|[一二两三四五六七八九十百]{1,5})'
    unit = r'(?:个|只|条|颗|把|枚|扇|片|块|面)'
    counts = []
    for match in re.finditer(re.escape(phrase), text):
        before = text[max(0, match.start() - 12):match.start()]
        found = re.search(number + r'\s*' + unit + r'\s*$', before)
        found = found or re.match(number + r'\s*' + unit, phrase)
        if found:
            counts.append(_number(found.group(1)))
        # The counted subject belongs to the assignment clause, not its chosen
        # colour/property words. "黑色橡胶" cannot replace "四个轮子" as a binding.
        start=max([text.rfind(mark,0,match.start()) for mark in '，,。；;！？!?\n'])+1
        endings=[text.find(mark,match.end()) for mark in '，,。；;！？!?\n']
        end=min([value for value in endings if value>=0] or [len(text)])
        clause=text[start:end]
        counted=list(re.finditer(number+r'\s*'+unit+r'\s*(?P<part>.{1,60}?)(?=(?:都|也)?(?:要(?:是)?|应为|是|改成|换成|改为|设为))',clause))
        if counted:
            matching=[item for item in counted if phrase in item.group(0) or item.group(0) in phrase or item.group('part') in phrase]
            if not matching:
                label=counted[0].group(0).strip()
                raise InstructionOutcome('needs_clarification','“'+phrase+'”没有明确绑定这句要求中的“'+label+'”。整条要求尚未应用，请明确定位这些部件。')
            counts.extend(_number(item.group(1)) for item in matching)
    return set(count for count in counts if count)


def resolve_bindings(bindings, evidence, text):
    """Resolve only image-backed IDs, never a model's free-form mesh guesses."""
    if not bindings:
        return []
    if not evidence:
        raise InstructionOutcome('needs_clarification', '缺少当前预览的真实部件采样，尚未应用这条要求。请先生成当前预览，或点选需要调整的部件。')
    if not isinstance(bindings, list) or len(bindings) > 20:
        raise InstructionOutcome('needs_clarification', '部件定位结果格式无效，整条要求尚未应用。')
    targets = {row['id']: row for row in evidence['targets']}; result = []; seen = set()
    catalog = evidence.get('method') == 'scene_part_catalog'
    maximum = MAX_CATALOG_TARGETS if catalog else MAX_TARGETS
    for binding in bindings:
        phrase = binding.get('phrase') if isinstance(binding, dict) else None
        purpose = binding.get('purpose') if isinstance(binding, dict) else None
        ids = binding.get('target_ids') if isinstance(binding, dict) else None
        if (not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 160 or phrase not in text
                or not isinstance(ids, list) or not 1 <= len(ids) <= maximum
                or not all(isinstance(ident, str) and ident in targets for ident in ids)
                or len(set(ids)) != len(ids) or purpose not in ('material','camera') or (phrase,purpose) in seen):
            raise InstructionOutcome('needs_clarification', '部件定位未对应到本次要求与当前画面的真实采样，整条要求尚未应用。请重新描述未定位的部位。')
        seen.add((phrase,purpose))
        rows = [targets[ident] for ident in ids]
        counts = _requested_count(text, phrase)
        count = spatial_instance_count(rows) if catalog else len({row['object_name'] for row in rows})
        if counts and counts != {count}:
            requested = '、'.join(str(value) for value in sorted(counts))
            raise InstructionOutcome('needs_clarification', '“' + phrase + '”要求定位 ' + requested + ' 个部件，当前只确认了 ' + str(count)
                                     + (' 组独立部件。当前方案未修改；请点选一个目标部位帮助识别。' if catalog
                                        else ' 个不同对象。整条要求尚未应用；请提供能看清其余部件的预览或补充点选。'))
        resolved = {'phrase': phrase, 'purpose': purpose, 'target_ids': list(ids), 'targets': [
            {key: row[key] for key in ('id', 'object_name', 'material_name')} for row in rows]}
        if catalog:
            resolved.update(instance_count=count, instance_basis='scene_geometry_components')
        result.append(resolved)
    return result


def resolve_semantic_bindings(bindings, evidence, analysis, version=None):
    """Resolve model-selected IDs using data only, without interpreting prose.

    The semantic planner separately binds each requirement to an exact source
    span. Its labels and optional instance-count commitments are supplied here
    as structured data. This function checks identity and measured geometry;
    it never extracts keywords, subject names or numbers from those labels.
    """
    if not isinstance(bindings, list) or len(bindings) > 20:
        raise SemanticEvidenceError('semantic_binding_invalid', '部件定位结果格式无效。')
    if not bindings:
        return []
    if version is not None and (type(version) is not int or version < 1):
        raise SemanticEvidenceError('semantic_binding_invalid', '部件来源版本无效。')
    public = public_evidence(evidence, analysis, version)
    if not public:
        raise SemanticEvidenceError('semantic_evidence_missing', '缺少当前版本可核验的部件证据。')
    targets = {row['id']: row for row in public['targets']}
    maximum = MAX_CATALOG_TARGETS if public['method'] == 'scene_part_catalog' else MAX_TARGETS
    result = []; seen = set()
    for binding in bindings:
        if (not isinstance(binding, dict)
                or set(binding) - {'phrase', 'purpose', 'target_ids', 'expected_instances'}):
            raise SemanticEvidenceError('semantic_binding_invalid', '部件定位包含未知字段。')
        phrase = binding.get('phrase'); purpose = binding.get('purpose'); ids = binding.get('target_ids')
        if (not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 160
                or purpose not in ('material', 'camera')
                or not isinstance(ids, list) or not 1 <= len(ids) <= maximum
                or any(not isinstance(ident, str) or ident not in targets for ident in ids)
                or len(set(ids)) != len(ids) or (phrase, purpose) in seen):
            raise SemanticEvidenceError('semantic_binding_invalid', '部件定位没有唯一对应到已提供的真实目标。')
        expected = binding.get('expected_instances')
        if 'expected_instances' in binding and (type(expected) is not int or not 1 <= expected <= 10000):
            raise SemanticEvidenceError('semantic_binding_invalid', '部件数量必须是有效的整数。')
        rows = [targets[ident] for ident in ids]
        count = None
        if public['method'] == 'scene_part_catalog':
            try:
                count = spatial_instance_count(rows)
            except InstructionOutcome:
                if expected is not None:
                    raise SemanticEvidenceError('semantic_count_unverified', '现有几何证据不能核验所要求的部件数量。') from None
        elif expected is not None:
            # Several meshes can form one wheel, and one mesh can contain
            # several wheels. Visible object names cannot prove a part count.
            raise SemanticEvidenceError('semantic_count_unverified', '可见表面采样不能证明完整的部件数量。')
        if expected is not None and count != expected:
            raise SemanticEvidenceError('semantic_count_mismatch',
                                        '要求覆盖 %d 个部件，当前证据对应 %d 组独立几何。' % (expected, count))
        resolved = {'phrase': phrase, 'purpose': purpose, 'target_ids': list(ids),
                    'targets': [{key: row[key] for key in ('id', 'object_name', 'material_name')} for row in rows]}
        if expected is not None:
            resolved['expected_instances'] = expected
        if count is not None:
            resolved.update(instance_count=count, instance_basis='scene_geometry_components')
        seen.add((phrase, purpose)); result.append(resolved)
    return result


def validate_semantic_binding_coverage(bindings, patch, analysis):
    """Require executable edits to cover exactly the grounded target scope.

    A global material edit affects every known user of that material. It is
    permitted only when all those pairs are explicitly bound; a chosen object
    never grants permission to edit other objects sharing its shader.
    """
    if not isinstance(bindings, list) or len(bindings) > 20 or not isinstance(patch, dict):
        raise SemanticEvidenceError('semantic_binding_invalid', '部件范围与修改结果格式无效。')
    known = _pairs(analysis); bound_materials = set(); bound_camera = set()
    for binding in bindings:
        if (not isinstance(binding, dict) or binding.get('purpose') not in ('material', 'camera')
                or not isinstance(binding.get('targets'), list) or not binding['targets']):
            raise SemanticEvidenceError('semantic_binding_invalid', '部件范围尚未完成真实目标核验。')
        for target in binding['targets']:
            if not isinstance(target, dict):
                raise SemanticEvidenceError('semantic_binding_invalid', '已定位部件身份无效。')
            pair = (target.get('object_name'), target.get('material_name'))
            if not all(isinstance(value, str) for value in pair) or pair not in known:
                raise SemanticEvidenceError('semantic_binding_invalid', '已定位部件不属于当前模型。')
            if binding['purpose'] == 'material': bound_materials.add(pair)
            else: bound_camera.add(pair[0])
    affected_materials = set()
    for field in ('object_material_overrides', 'material_overrides'):
        rows = patch.get(field, [])
        if not isinstance(rows, list):
            raise SemanticEvidenceError('semantic_binding_invalid', '材质修改结果格式无效。')
        seen = set(); local = field == 'object_material_overrides'
        identity = {'object_name', 'material_name'} if local else {'name'}
        for row in rows:
            if not isinstance(row, dict) or not identity <= set(row):
                raise SemanticEvidenceError('semantic_binding_invalid', '材质修改缺少目标身份。')
            key = (row['object_name'], row['material_name']) if local else row['name']
            valid_key = all(isinstance(value, str) for value in key) if local else isinstance(key, str)
            if not valid_key or key in seen:
                raise SemanticEvidenceError('semantic_binding_invalid', '材质修改的目标身份无效或重复。')
            seen.add(key)
            if not set(row) - identity:
                raise SemanticEvidenceError('semantic_binding_coverage', '已定位的材质缺少可执行的修改参数。')
            affected = {key} if local else {pair for pair in known if pair[1] == key}
            if not affected or not affected <= known:
                raise SemanticEvidenceError('semantic_binding_invalid', '材质修改引用了模型之外的目标。')
            affected_materials.update(affected)
    if affected_materials != bound_materials:
        raise SemanticEvidenceError('semantic_binding_coverage', '材质修改没有准确覆盖已定位的部件范围。')
    shots = patch.get('shots', [])
    if not isinstance(shots, list) or any(not isinstance(shot, dict) for shot in shots):
        raise SemanticEvidenceError('semantic_binding_invalid', '镜头修改结果格式无效。')
    camera_objects = {shot['target_object'] for shot in shots
                      if isinstance(shot.get('target_object'), str) and shot['target_object']}
    if camera_objects != bound_camera:
        raise SemanticEvidenceError('semantic_binding_coverage', '镜头目标没有准确对应到已定位的部件范围。')


def material_assignments(text):
    """Expand explicitly shared material predicates without guessing targets."""
    from intelligence_rules import instruction_clauses,material_clause
    assignments=[]
    # Retain the existing whitespace-separated assignment form as well as
    # coordinated comma/list syntax; each subject still requires real binding.
    for segment in re.split(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])',text):
        for clause in instruction_clauses(segment):
            parsed=material_clause(clause)
            if parsed:
                assignments.extend({'phrase':subject,'value':parsed['value']} for subject in parsed['subjects'])
    return assignments


def _whole_target(phrase):
    return bool(re.fullmatch(r'(?:整个(?:产品|模型|物体)|(?:所有|全部)(?:部件|材质|产品)|(?:产品|模型)整体)(?:的)?(?:材质|颜色|表面)?',phrase))


def whole_material_request(text):
    return any(_whole_target(item['phrase']) for item in material_assignments(text))


def named_global_material(name, text):
    # A word such as "橡胶" used as a desired property does not authorize editing
    # a same-named material shared with the cabinet or other unrelated objects.
    return bool(re.search(r'(?:^|[，,。；;\n])\s*(?:请)?(?:把|将|让)?(?:全局材质|共用材质)(?:名为|名叫|叫)?\s*[“「\"\']?'+re.escape(name)+r'(?:[”」\"\']|(?=\s|$|[，,。；;改设调的]))',text))


def validate_binding_coverage(bindings, patch, text, analysis):
    """Binding four parts is not execution if the patch assigns only two."""
    material_pairs={(entry.get('object_name'),entry.get('material_name'))
                    for entry in patch.get('object_material_overrides',[]) if len(entry)>2}
    camera_objects={entry.get('target_object') for entry in patch.get('shots',[]) if entry.get('target_object')}
    global_names={entry.get('name') for entry in patch.get('material_overrides',[]) if len(entry)>1}
    for binding in bindings:
        if binding['purpose']=='material':
            missing={(row['object_name'],row['material_name']) for row in binding['targets']}-material_pairs
            action='材质修改'
        else:
            missing={row['object_name'] for row in binding['targets']}-camera_objects
            action='镜头目标'
        if missing:
            raise InstructionOutcome('needs_clarification','“'+binding['phrase']+'”仍有 '+str(len(missing))
                                     +' 个已定位目标未包含在'+action+'中，整条要求尚未应用。请重试或补充该部位的要求。')
    known=_pairs(analysis)
    for assignment in material_assignments(text):
        phrase=assignment['phrase']
        if _whole_target(phrase):
            missing={pair for pair in known if pair not in material_pairs and pair[1] not in global_names}
            if missing:
                raise InstructionOutcome('needs_clarification','“'+phrase+'”的材质要求尚未覆盖全部部件，整条要求尚未应用。')
            continue
        explicit_globals={pair[1] for pair in known if named_global_material(pair[1],phrase)}
        if explicit_globals:
            if not explicit_globals<=global_names:
                raise InstructionOutcome('needs_clarification','“'+phrase+'”未包含在本次全局材质修改中，整条要求尚未应用。')
            continue
        related=[binding for binding in bindings if binding['purpose']=='material'
                 and (binding['phrase'] in phrase or phrase in binding['phrase'])]
        if related:
            continue
        literal={pair for pair in known if pair[0] in phrase}
        if literal and literal<=material_pairs:
            continue
        raise InstructionOutcome('needs_clarification','“'+phrase+'”的材质要求尚未定位或未包含在修改中，整条要求尚未应用。请提供能看清该部位的预览或补充点选。')


def missing_parts_message(text, bindings):
    """Quote remaining user phrases without pretending to know their mesh IDs."""
    bound = [binding['phrase'] for binding in bindings]
    phrases = []
    for clause in re.split(r'[，,。；;\n]', text):
        found = re.match(r'\s*(?:请)?(?:把|将|让)?(.{1,60}?)(?:都|也)?(?:要(?:是)?|是|改成|换成|改为|设为)', clause)
        if not found:
            continue
        phrase = found.group(1).strip()
        if phrase and not any(value in phrase or phrase in value for value in bound):
            phrases.append(phrase)
    label = '、'.join('“' + phrase + '”' for phrase in list(dict.fromkeys(phrases))[:4])
    return ('尚未可靠定位' + (label or '要求中的部分部件')
            + '，整条要求尚未应用。请提供能看清这些部位的当前预览，或只点选仍未定位的部件。')

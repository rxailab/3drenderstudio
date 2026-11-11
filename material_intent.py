"""Bounded material-property completion after actual target grounding.

This module never identifies a part or adds a material assignment. The caller
must validate the patch's object/material identities first. Missing finish
parameters reuse existing application presets; explicit user numbers remain
authoritative. Parameter checks do not certify a rendered visual appearance.
"""
import colorsys
import copy
import math
import re

import target_evidence
from intelligence_rules import enforce_protections


# The same roughness values already offered by the material preset UI.
FINISH_ROUGHNESS = {'brushed_metal': .30, 'painted_metal': .28, 'polished_metal': .09}
# A broad consistency boundary, not a measured definition of visible gloss.
# Keep stylistic latitude while refusing clearly matte values such as 0.9/1.
MAX_GLOSS_ROUGHNESS = .60
_NUMBER = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))'
_ROUGHNESS = re.compile(r'(?:粗糙度|\broughness\b)\s*(?:(?:设置|调整|设|改|换)(?:为|成|到)|是|为|等于|=|:|：)?\s*'
                        + _NUMBER + r'\s*([%％]?)', re.I)


class MaterialIntentError(ValueError):
    """A short, correctable property conflict without model-suggested work."""

    def __init__(self, message, code='material_property_conflict'):
        super().__init__(message)
        self.code = code


def _positive_finish(value):
    if re.search(r'(?:不(?:要|用|需(?:要)?)?|无需|不必)反光|无光泽|没有光泽|(?:不(?:要|用|需(?:要)?)?|无需|不必)光泽|哑光|磨砂|消光|不发亮|\b(?:matte|nonreflective)\b', value, re.I):
        return False
    return bool(re.search(r'反光|有光泽|光泽|亮面|光亮|亮光|镜面|\b(?:glossy|reflective|shiny)\b', value, re.I))


def _explicit_roughness(value, phrase):
    values = []
    for match in _ROUGHNESS.finditer(value):
        number = float(match.group(1)) / (100 if match.group(2) else 1)
        if not math.isfinite(number) or not 0 <= number <= 1:
            raise MaterialIntentError('“' + phrase + '”的粗糙度需要在 0–1 范围内，当前方案未修改。',
                                      code='material_user_constraint')
        values.append(number)
    if len(set(values)) > 1:
        raise MaterialIntentError('“' + phrase + '”包含不同的粗糙度目标，请保留一个明确数值。',
                                  code='material_user_constraint')
    return values[0] if values else None


def _related(binding, phrase):
    return (isinstance(binding, dict) and binding.get('purpose') == 'material'
            and isinstance(binding.get('phrase'), str) and binding['phrase']
            and (binding['phrase'] in phrase or phrase in binding['phrase']))


def _entries(patch, phrase, bindings):
    """Return only existing entries covered by the same grounded subject."""
    pairs = {(row.get('object_name'), row.get('material_name'))
             for binding in bindings or [] if _related(binding, phrase)
             for row in binding.get('targets', []) if isinstance(row, dict)}
    whole = target_evidence._whole_target(phrase)
    rows = []
    for entry in patch.get('object_material_overrides', []):
        if not isinstance(entry, dict): continue
        name = entry.get('object_name')
        literal = isinstance(name, str) and name and bool(re.search(r'(?<![\w.])' + re.escape(name) + r'(?![\w.])', phrase))
        if whole or literal or (entry.get('object_name'), entry.get('material_name')) in pairs:
            rows.append(entry)
    for entry in patch.get('material_overrides', []):
        if not isinstance(entry, dict) or not isinstance(entry.get('name'), str): continue
        if whole or target_evidence.named_global_material(entry['name'], phrase):
            rows.append(entry)
    return rows


def _prior(plan, entry, current_values=None):
    """Known explicit plan values, without guessing imported shader values."""
    plan = plan or {}; result = {}
    material = entry.get('material_name', entry.get('name'))
    for item in plan.get('material_overrides', []):
        if isinstance(item, dict) and item.get('name') == material:
            result.update(item)
    if 'object_name' in entry:
        for item in plan.get('object_material_overrides', []):
            if (isinstance(item, dict) and item.get('object_name') == entry['object_name']
                    and item.get('material_name') == entry.get('material_name')):
                result.update(item)
    # A verified rendered checkpoint can be newer than the plan's historical
    # overrides. Prefer its measured constant sockets; never infer a preset
    # from them and never treat a linked socket default as a measured value.
    rows = [row for row in current_values or [] if isinstance(row, dict)
            and row.get('material_name') == material
            and ('object_name' not in entry or row.get('object_name') == entry['object_name'])]
    if rows:
        for field in ('roughness', 'metallic', 'transmission'):
            values = [row.get('values', {}).get(field) for row in rows
                      if field not in row.get('linked_fields', [])]
            if (len(values) == len(rows) and all(type(value) in (int, float)
                    and math.isfinite(value) and 0 <= value <= 1 for value in values)
                    and len(set(values)) == 1):
                result[field] = values[0]
            else:
                result.pop(field, None)
    return result


def relative_gloss(value):
    """A requested increase in a named surface's finish, not plain brightness."""
    return _positive_finish(value) and bool(re.search(
        r'(?:光泽|反光).*(?:不够|不足|偏弱|太弱|更强|增强|增加|提高|再|更多)|'
        r'(?:更|再|增强|增加|提高).*(?:光泽|反光)', value, re.I))


def _gentle(value):
    return bool(re.search(r'轻微|微调|稍微|略微|小幅|一点点', value))


def _relative_target(current, value):
    if type(current) not in (int, float) or not math.isfinite(current) or not 0 < current <= 1:
        return None
    # A measured 35% roughness reduction is an assessable finish attempt, not
    # a promise about the next image. Explicit gentle language takes priority.
    target = current * (.90 if _gentle(value) else .65)
    return round(target, 6) if round(target, 6) < current else None


def _requests_red(value):
    if re.search(r'非红色|不(?:要|是|用|需(?:要)?)?红色|无需红色|\bnot\s+red\b', value, re.I): return False
    return bool(re.search(r'红色|\bred\b', value, re.I))


def _red_color(color):
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color): return False
    channels = [int(color[index:index+2], 16) / 255 for index in (1, 3, 5)]
    hue, saturation, brightness = colorsys.rgb_to_hsv(*channels)
    angle = hue * 360
    # Includes warm reds, burgundy and muted red finishes; excludes clearly
    # blue/green, neutral white/grey and black. No exact brand-red is implied.
    return (angle <= 30 or angle >= 330) and saturation >= .16 and brightness >= .10


def complete_missing(patch, text, resolved_bindings, protections=None, plan=None, current_values=None):
    """Complete missing roughness on existing, already grounded assignments.

    An explicit patch value is never overwritten. A missing value uses an
    explicit user roughness first, otherwise the existing preset default only
    when a reflective/glossy finish was requested. Current roughness is not
    inherited for such a request: an old 0.9 value must not survive unnoticed.
    """
    result = copy.deepcopy(patch)
    enforce_protections(result, protections or [])
    for assignment in target_evidence.material_assignments(text):
        phrase, value = assignment['phrase'], assignment['value']
        exact = _explicit_roughness(value, phrase)
        if exact is None and not _positive_finish(value): continue
        for entry in _entries(result, phrase, resolved_bindings):
            if 'roughness' in entry: continue
            prior = _prior(plan, entry, current_values)
            preset = entry.get('preset', prior.get('preset'))
            desired = exact if exact is not None else (_relative_target(prior.get('roughness'), value)
                      if relative_gloss(value) else FINISH_ROUGHNESS.get(preset))
            if desired is not None: entry['roughness'] = desired
    enforce_protections(result, protections or [])
    return result


def validate(patch, text, resolved_bindings, protections=None, plan=None, current_values=None):
    """Check explicit red/finish intent after completion and normal grounding."""
    enforce_protections(patch, protections or [])
    for assignment in target_evidence.material_assignments(text):
        phrase, value = assignment['phrase'], assignment['value']
        exact = _explicit_roughness(value, phrase)
        finish = _positive_finish(value); red = _requests_red(value)
        if exact is None and not finish and not red: continue
        entries = _entries(patch, phrase, resolved_bindings)
        if not entries:
            raise MaterialIntentError('“' + phrase + '”的材质属性还没有对应到已定位部位，当前方案未修改。',
                                      code='material_target_unresolved')
        for entry in entries:
            if exact is not None or finish:
                roughness = entry.get('roughness')
                if type(roughness) not in (int, float) or not math.isfinite(roughness) or not 0 <= roughness <= 1:
                    raise MaterialIntentError('“' + phrase + '”的光泽要求还缺少可核验的粗糙度参数，当前方案未修改。')
                if exact is not None:
                    if not math.isclose(roughness, exact, rel_tol=0, abs_tol=1e-9):
                        raise MaterialIntentError('“' + phrase + '”的粗糙度参数与明确数值要求不一致，当前方案未修改。')
                elif roughness > MAX_GLOSS_ROUGHNESS and not relative_gloss(value):
                    raise MaterialIntentError('“' + phrase + '”要求反光或有光泽，但修改仍使用明显偏哑光的粗糙度，当前方案未修改。')
                elif relative_gloss(value):
                    current = _prior(plan, entry, current_values).get('roughness')
                    if type(current) in (int, float) and math.isfinite(current):
                        decrease = current - roughness
                        minimum = 0 if _gentle(value) else min(.02, current * .15)
                        if decrease <= 0 or decrease + 1e-9 < minimum:
                            raise MaterialIntentError('“' + phrase + '”要求进一步增加光泽，但粗糙度未形成对应的降低，当前方案未修改。')
            if relative_gloss(value):
                # Finish-only edits preserve the product's actual colour and
                # shader identity. The LLM may not make a cabinet glossier by
                # silently recolouring it or replacing its material preset.
                explicit_type = bool(re.search(r'橡胶|金属|镀铬|不锈钢|拉丝|塑料|玻璃|陶瓷|木|皮革|织物|布料', value))
                explicit_color = bool(re.search(r'[红橙黄绿青蓝紫黑白灰银金棕]色|#[0-9A-Fa-f]{6}', value))
                unrelated = (('preset' in entry and not explicit_type)
                             or ('color' in entry and not explicit_color)
                             or ('metallic' in entry and not re.search(r'金属度', value))
                             or ('transmission' in entry and not re.search(r'透明|透射', value)))
                if unrelated:
                    raise MaterialIntentError('“' + phrase + '”只要求增加光泽，修改不能额外改变颜色或材质类型，当前方案未修改。')
            if red:
                color = entry.get('color', _prior(plan, entry).get('color'))
                if not _red_color(color):
                    raise MaterialIntentError('“' + phrase + '”的颜色参数不符合要求的红色，当前方案未修改。')

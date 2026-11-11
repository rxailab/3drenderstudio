"""Conservative selection of rendered candidates, never a realism guarantee.

Model scores are uncalibrated preferences. A small explicit improvement margin,
matching evaluator identity and non-regressing measured evidence are required
before the application automatically adopts a trial.
"""
import math

EVALUATION_PROTOCOL = 'product-preview-v2'
MIN_SCORE_GAIN = .02
MIN_OPAQUE_SAMPLES = 30


def instruction_protected_roots(plan):
    """Carry explicit instruction limits through its automatic preview trials.

    A later manual plan save is a new explicit decision. History is never used
    to invent permanent restrictions for unrelated future instructions.
    """
    if plan.get('source') == 'user':
        return set()
    instruction = plan.get('last_instruction', {})
    fields = instruction.get('protected_fields', []) if isinstance(instruction, dict) else []
    return {field for field in fields if isinstance(field, str)} if isinstance(fields, list) else set()


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _sampling(output):
    metrics = output.get('metrics', {})
    product = metrics.get('product', {}) if isinstance(metrics, dict) else {}
    return product if isinstance(product, dict) else {}


def _framing(output):
    checks = output.get('checks', {})
    if isinstance(checks, dict) and checks.get('kind') == 'complete_product':
        return checks
    framing = _sampling(output).get('framing', {})
    return framing if isinstance(framing, dict) else {}


def measurement_guard(before, after):
    """Block new measured damage; missing data must not masquerade as success."""
    old_frame, new_frame = _framing(before), _framing(after)
    if old_frame.get('kind') == 'complete_product' and type(old_frame.get('clipped')) is bool:
        if new_frame.get('kind') != 'complete_product' or type(new_frame.get('clipped')) is not bool:
            return False, '新候选缺少可比较的完整构图检查，保留此前画面。'
        if old_frame['clipped'] is False and new_frame['clipped'] is True:
            return False, '新候选出现完整产品裁切，保留此前画面。'
    old, new = _sampling(before), _sampling(after)
    old_count, new_count = old.get('eligible_opaque_samples'), new.get('eligible_opaque_samples')
    old_fraction, new_fraction = old.get('opaque_clipped_fraction'), new.get('opaque_clipped_fraction')
    sufficient_old = type(old_count) is int and old_count >= MIN_OPAQUE_SAMPLES and _finite_number(old_fraction) and 0 <= old_fraction <= 1
    sufficient_new = type(new_count) is int and new_count >= MIN_OPAQUE_SAMPLES and _finite_number(new_fraction) and 0 <= new_fraction <= 1
    if sufficient_old:
        if not sufficient_new or old.get('method') != new.get('method'):
            return False, '新候选缺少同方法的有效产品曝光采样，保留此前画面。'
        if new_fraction > .12 and new_fraction > old_fraction + .02:
            return False, '新候选的不透明产品高光剪切明显增多，保留此前画面。'
    return True, ''


def compare_candidates(before, after, *, require_ai=False):
    """Return a serializable explanation for a single matched shot pair."""
    result = {'comparable': False, 'guard_passed': False, 'accepted': False,
              'minimum_gain': MIN_SCORE_GAIN, 'reason': ''}
    older, newer = before.get('review', {}), after.get('review', {})
    if not isinstance(older, dict) or not isinstance(newer, dict):
        result['reason'] = '缺少完整的候选检查记录，保留此前画面。'
        return result
    if before.get('shot_id') and after.get('shot_id') and before['shot_id'] != after['shot_id']:
        result['reason'] = '两张图片属于不同镜头，不能作为自动优化的前后对比。'
        return result
    source = older.get('source')
    if source not in ('ai', 'rules') or source != newer.get('source') or (require_ai and source != 'ai'):
        result['reason'] = '两次检查的评分来源不可比较，保留此前画面。'
        return result
    if older.get('evaluation_protocol', 'legacy') != newer.get('evaluation_protocol', 'legacy'):
        result['reason'] = '两次检查使用不同评估版本，保留此前画面。'
        return result
    if source == 'ai':
        if any(not isinstance(older.get(key), str) or not older[key].strip() or older[key] != newer.get(key) for key in ('provider', 'model')):
            result['reason'] = '视觉服务商或模型不一致，不能据此自动采用新图。'
            return result
    else:
        old, new = _sampling(before), _sampling(after)
        if any(type(item.get('eligible_opaque_samples')) is not int or item['eligible_opaque_samples'] < MIN_OPAQUE_SAMPLES for item in (old, new)) or not old.get('method') or old.get('method') != new.get('method'):
            result['reason'] = '基础规则缺少足够且可比较的产品采样，保留此前画面。'
            return result
    scores = [older.get('score'), newer.get('score')]
    if any(not _finite_number(score) or not 0 <= score <= 1 for score in scores):
        result['reason'] = '评分缺失或超出有效范围，保留此前画面。'
        return result
    gain = scores[1] - scores[0]
    result.update(comparable=True, before_score=scores[0], after_score=scores[1], gain=round(gain, 6))
    passed, reason = measurement_guard(before, after)
    result['guard_passed'] = passed
    if not passed:
        result['reason'] = reason
        return result
    if gain <= MIN_SCORE_GAIN + 1e-12:
        result['reason'] = '新候选评分下降，保留此前画面。' if gain < -MIN_SCORE_GAIN else '评分改善未超过采用阈值，保留此前画面。'
        return result
    result.update(accepted=True, reason='同一评估来源下评分改善超过采用阈值，且已有测量未发现明确退化。')
    return result

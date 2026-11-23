"""Laying a page out: render each version of its job in the headless browser, check the measured layout, and fall
back to a lighter version when a check fails. Split out of ``brandkit_set``, which still re-exports every name.
"""

import json
import re
import shutil

import brandkit_render

from listing_kinds import KINDS
from listing_scenes import swappable_scenes
from listing_theme import MARK_NOTES, mark_note
from listing_jobs import jobs_for


SOFT_CHECKS = ('frame_cuts_part',)


def hard_issues(qa):
    """The layout problems that make a page wrong; a close-up that still clips a small part is reported on its own."""
    return [row for row in brandkit_render.layout_issues(qa) if row.get('check') not in SOFT_CHECKS]


def _measured(qa):
    """What the browser measured, for the art director: where each picture lies and whether a whole product is inside the page,
    which frames are close-ups by design, text sizes at phone width. Ground truth it must not contradict."""
    width, height = (qa.get('canvas') or [1500, 1500])[:2]
    elements = qa.get('elements', [])
    texts = [row for row in elements if row.get('kind') == 'text']
    small = min((row.get('equiv_px_at_360', 99) for row in texts if row.get('tier') in ('headline', 'important')), default=None)
    pictures = []; by_id = {row.get('id'): row for row in elements}
    for row in elements:
        if row.get('kind') in ('product_image', 'detail_image') and row.get('w'):
            if row.get('inframe') and by_id.get(row['id'] + '_frame'):
                row = {**by_id[row['id'] + '_frame'], 'id': row['id'], 'crop': row.get('crop')}       # what shows is the frame, not the picture behind it
            bleed = row.get('bleed')
            inside = bleed or (row['x'] >= -1 and row['y'] >= -1 and row['x'] + row['w'] <= width + 1 and row['y'] + row['h'] <= height + 1)
            whole = row.get('crop') == 'contain' and not bleed
            pictures.append({'id': row['id'], 'box_px': [round(row['x']), round(row['y']), round(row['w']), round(row['h'])],
                             'shows': 'the whole product, uncropped' if whole and inside else 'a crop by design (close-up or scene frame)',
                             'fully_inside_page': bool(inside)})
    return {'layout_checks_passed': not hard_issues(qa), 'canvas_px': [width, height], 'pictures': pictures,
            'smallest_important_text_px_at_360': small, 'text': [{'id': row['id'], 'lines': row.get('lines'), 'text': row.get('text', '')[:200]}
                                                                  for row in texts][:14],
            # layout notes, without those about a brand mark left out for lack of room: that is said to the seller, and is
            # no fault of the page for a review to mark down
            'notes': [line for line in qa.get('notes', []) if not any(key in str(line) for key, _ in MARK_NOTES)][:3]}


def scene_vetoes(vetoes, checks):
    """The page review's vetoes, with the scenes' own product checks (``checks``: {'scene1': 'passed' | 'failed' | ...} for
    the scenes on the page) taken as the facts they are. Returns (vetoes, a note for the page or None).

    A scene that failed its own check is a veto whatever the review saw. And when every scene on the page passed its
    own check (made against a render of the product in the same state, from the same side), a 'scene_product_mismatch'
    from the page review does not stand: it looked at a small tile beside a picture of the product at rest, and doors
    that stand open hide the handles it looks for."""
    vetoes = list(vetoes or [])
    failed = sorted(key for key, state in checks.items() if state == 'failed')
    if failed:
        return vetoes + [{'kind': 'scene_product_mismatch', 'evidence': '场景图的产品核对未通过：%s' % '、'.join(failed)}], None
    if checks and all(state == 'passed' for state in checks.values()) and any(row.get('kind') == 'scene_product_mismatch' for row in vetoes):
        return ([row for row in vetoes if row.get('kind') != 'scene_product_mismatch'],
                '视觉审核认为场景里的产品与参考图不符；这些场景图各自的产品核对（与同一姿态的渲染图比对）已经通过，以核对为准。')
    return vetoes, None


def _better(a, b):
    """Whether verdict a beats verdict b: fewer vetoes first, then the higher score."""
    if b is None:
        return True
    return (len(a['vetoes']), -a['score']) < (len(b['vetoes']), -b['score'])


def _cut_text(qa, problems):
    """The ids of the texts a page cannot hold (wider than their box, or past the canvas edge): sorted, empty when none."""
    texts = {row.get('id') for row in qa.get('elements', []) if row.get('kind') == 'text'}
    return sorted({str(row.get('id')) for row in problems
                   if row.get('check') == 'text_overflow' or (row.get('check') == 'out_of_canvas' and row.get('id') in texts)})


def lay_out(kind, ctx, out, work):
    """Lay a page out: the first version that passes the layout checks (or the one with the fewest problems), then,
    when a vision model is configured, the art director's review and up to two rounds of fixes.
    Returns (qa, notes, review record)."""
    import brandkit_review
    candidates = jobs_for(kind, ctx)
    if not candidates:
        raise ValueError('「%s」没有可用的内容' % KINDS[kind][0])
    try:            # the versions this page could be laid out in, kept beside it (scripts/brandkit_preview.py re-renders them)
        folder = out.parent / 'jobs'; folder.mkdir(exist_ok=True)
        (folder / (kind + '.json')).write_text(json.dumps(candidates[:12], ensure_ascii=False, default=str), encoding='utf-8')
    except (OSError, TypeError):
        pass
    best = None; best_key = None
    for number, job in enumerate(candidates):
        if best_key and best_key[1] == 0 and job.get('_group', number) != best_key[2]:
            break           # a version without hard problems, and none of its other crops is clean: keep it, not a leaner layout
        target = out if number == 0 else out.with_name('%s.try%d.png' % (out.stem, number))
        qa = brandkit_render.render(kind, job, target, work)
        problems = brandkit_render.layout_issues(qa)
        # A crop that clips a small part only decides between crops of one layout; it never trades away content.
        hard = [row for row in problems if row.get('check') not in SOFT_CHECKS]
        # A version whose words are cut off is never made (below): any version that holds its words wins over it, even one
        # with more of the other problems.
        key = (bool(_cut_text(qa, problems)), len(hard), job.get('_group', number), len(problems) - len(hard))
        if best is None or key < best_key:
            if best and best[2] != out:
                best[2].unlink(missing_ok=True)
            best = (qa, problems, target, number); best_key = key
        elif target != out:
            target.unlink(missing_ok=True)
        if not problems:
            break
    qa, problems, target, number = best
    if target != out:
        shutil.move(str(target), str(out))
    job = candidates[number]
    notes = []
    def layout(row):          # what a reader would call a different version: not where a line points or a crop sits
        plain = {key: value for key, value in row.items() if not key.startswith('_')}
        for key in ('features', 'details'):
            if isinstance(plain.get(key), list):
                plain[key] = [{k: v for k, v in item.items() if k not in ('anchor', 'focus', 'other_patch')} if isinstance(item, dict) else item
                              for item in plain[key]]
        return plain
    if layout(job) != layout(candidates[0]):
        notes.append('为通过版式检查，这张图用了精简版本（第 %d 种排法）。' % (job.get('_group', number) + 1))
    if any(row.get('check') == 'frame_cuts_part' for row in problems):
        notes.append('特写框仍切到了小部件（%s），换几种取景都没能避开。' % '、'.join(sorted({row['id'].split('×')[-1] for row in problems if row.get('check') == 'frame_cuts_part'})))
    record = {'status': 'not_verified', 'rounds': 0, 'applied': []}
    if ctx.get('review') and not ctx.get('review_refused'):
        reference = ctx['assets']['hero']['path']
        facts = ctx.get('facts') or brandkit_review.facts_of(ctx['product'])
        scenes = swappable_scenes(ctx)
        try:
            verdict = brandkit_review.checked(kind, job, out, ctx['product'], reference, _measured(qa), ctx.get('facts'))
        except Exception as exc:
            verdict = None; notes.append('视觉审核没有完成（%s），这张图未经审核。' % str(exc)[:80])
            if re.search(r'HTTP 40[123]', str(exc)):
                ctx['review_refused'] = str(exc)[:80]        # quota or key refused: the other pages do not ask again
        if verdict:
            first = verdict; tried = {number}; current = (job, verdict, qa)
            for round_number in range(1, brandkit_review.MAX_REVISIONS + 1):
                if brandkit_review.passed(current[1]):
                    break
                revised, applied = brandkit_review.revise(kind, current[0], current[1], facts, scenes)
                if revised in ('next_layout', 'default_text'):
                    stock = revised == 'default_text'
                    following = next((index for index in range(len(candidates)) if index not in tried
                                      and (not stock or candidates[index].get('_lines') == 'stock')), None)
                    if following is None:
                        break
                    tried.add(following); revised = candidates[following]
                if revised is None:
                    break
                trial = out.with_name('%s.rev%d.png' % (out.stem, round_number))
                trial_qa = brandkit_render.render(kind, revised, trial, work)
                if hard_issues(trial_qa):
                    trial.unlink(missing_ok=True); notes.append('审核建议的修改（%s）没有通过版式检查，未采用。' % '、'.join(applied)); break
                try:
                    trial_verdict = brandkit_review.checked(kind, revised, trial, ctx['product'], reference, _measured(trial_qa), ctx.get('facts'))
                except Exception:
                    trial.unlink(missing_ok=True); break
                record['rounds'] = round_number
                if _better(trial_verdict, current[1]):
                    shutil.move(str(trial), str(out)); current = (revised, trial_verdict, trial_qa); record['applied'] += applied; qa = trial_qa
                else:
                    trial.unlink(missing_ok=True); notes.append('第 %d 轮修改（%s）没有让评分变好，保留原版。' % (round_number, '、'.join(applied)))
                    break
            verdict = current[1]
            shown = {key: (ctx['scenes'][int(key[5:]) - 1] if int(key[5:]) <= len(ctx['scenes']) else {}).get('check')
                     for key in current[0].get('assets', {}) if key.startswith('scene') and key[5:].isdigit()}
            settled, said = scene_vetoes(verdict['vetoes'], shown)
            verdict = {**verdict, 'vetoes': settled}
            if said:
                notes.append(said)
            record.update(status='passed' if brandkit_review.passed(verdict) and not record['applied'] else
                                 'revised' if brandkit_review.passed(verdict) else 'failed',
                          score=verdict['score'], first_score=first['score'], scores=verdict['scores'], model=verdict.get('model'),
                          vetoes=[row['kind'] for row in verdict['vetoes']], summary=verdict['summary'], refuted=verdict.get('refuted', 0),
                          issues=[{'region': row.get('region', ''), 'evidence': row.get('evidence', '')} for row in verdict['issues']])
            if record['applied']:
                notes.append('按视觉审核修改：%s（%d → %d 分）。' % ('、'.join(record['applied']), first['score'], verdict['score']))
            if record['status'] == 'failed':
                reasons = '；'.join(row.get('evidence', '') for row in verdict['vetoes'] + verdict['issues'] if row.get('evidence'))[:220]
                notes.append('视觉审核未通过（%d 分%s）%s' % (verdict['score'], '，否决项 ' + '、'.join(record['vetoes']) if record['vetoes'] else '',
                                                        '：' + reasons if reasons else '。'))
    problems = hard_issues(qa)
    # Text the page cannot hold is cut off at the canvas edge ("06_DIAGONAL_C…" on the poster): the browser measured it
    # (text_overflow), but the page with the fewest problems used to be kept anyway. A page whose words do not fit is not made.
    clipped = _cut_text(qa, problems)
    if clipped:
        out.unlink(missing_ok=True)
        raise ValueError('文字放不下（%s），试过的排法都会把字截断，这张图没有生成；请把这段文字改短' % '、'.join(clipped))
    if problems:
        notes.append('版式检查仍有 %d 处问题：%s。' % (len(problems), '、'.join(sorted({row['check'] for row in problems}))))
    lacking = sorted({str(row.get('id', '')).rsplit(' ', 1)[0] for row in qa.get('issues', []) if row.get('check') == 'font_missing'})
    if lacking:
        chinese = [name for name in lacking if 'SC' in name or 'CJK' in name]
        notes.append(('中文字体 %s 还没有下载成功，这张图暂用系统中文字体（如微软雅黑）；联网后重新生成即可。' % '、'.join(chinese)) if chinese == lacking
                     else '字体 %s 没有找到，已用系统字体代替；请确认 brandkit/fonts 完整，或联网后重新生成。' % '、'.join(lacking))
    for line in qa.get('notes', [])[:4]:
        notes.append(mark_note(line))
    # The same page as an editable design file: live text, vector shapes and named layers (brandkit/engine/svg_export.js).
    final = current[0] if 'current' in locals() else job
    try:
        brandkit_render.export_svg(kind, final, out.parent / 'svg' / (out.stem + '.svg'), work)
    except Exception as exc:          # the PNG stands without it
        notes.append('可编辑 SVG 没有导出：%s' % str(exc)[:120])
    return qa, notes, record

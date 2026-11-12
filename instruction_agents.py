"""Specialist agents that finish what the planner could not ground.

The planner reads the whole instruction and returns a complete decision in one
response. That is the right shape for most requests and the wrong one for a
requirement that fails on a single narrow point: which parts a phrase means,
or which surface values a description implies. Each agent here answers exactly
one such question with a small schema of its own.

    planner    whole instruction -> requirements, patches, bindings  (semantic_planner)
    verifier   the planner's part selections -> targets that do not belong in them
    locator    one requirement   -> the catalogue targets it names
    finisher   one requirement   -> surface values for the located targets
    executor   data only         -> builds the row and has it validated   (no model call)

No agent's answer is applied directly. A specialist result is turned into an
ordinary planner row and goes through the same validation as every other row:
authorisations quoted from the user's own words, protections, exact target
scope, measured effect. An agent can therefore make a pending requirement
executable; it cannot widen what the user permitted.

The team is bounded: a fixed number of model calls per instruction, and a
transport failure simply ends the stage with the planner's result intact.
"""
import copy
import json
import os

import target_evidence

ENV_ENABLED = 'AI_AGENT_TEAM'
ENV_MAX_CALLS = 'AI_AGENT_MAX_CALLS'
ENV_VERIFY = 'AI_AGENT_VERIFY'
DEFAULT_MAX_CALLS = 6
MAX_ROWS = 4
MIN_CONFIDENCE = .55
# A second opinion removes a target only when it is this sure, and never adds one.
VERIFY_CONFIDENCE = .7
MATERIAL_ROOTS = ('material_overrides', 'object_material_overrides')

# Why a requirement was not executable -> which specialist can still help.
RELOCATE = {'semantic_binding_invalid', 'semantic_count_mismatch', 'semantic_count_unverified',
            'semantic_binding_coverage', 'semantic_evidence_missing', 'enum'}
UNRESOLVED_REASONS = ('not_in_catalogue', 'ambiguous', 'hidden_or_too_small', 'not_a_part')
# What the user is told when a specialist looked and still could not finish.
PENDING_REASONS = {
    'not_in_catalogue': '已自动查找，模型的部件中没有找到这项要求所指的部位；可以补充它的位置或换一种说法。',
    'ambiguous': '已自动查找，但有多个相似部件无法区分；可以在预览中点选一个示例部位，或补充位置描述。',
    'hidden_or_too_small': '已自动查找，所指部位在模型中过小或被完全遮挡，无法可靠定位。',
    'not_a_part': '已自动查找，这项要求描述的不是模型中的某个部件；可以换一种说法。',
    'duplicate_selection': '已自动查找，但定位结果互相重叠，本次未应用。',
}

VERIFIER_PROMPT = '''You check part selections that another agent made on a 3D product model. You do not select parts yourself and you do not choose any change.
You receive the user's full instruction, the current render (CURRENT_RENDER), labelled part-catalogue images (PART_CATALOG_n) showing every target with its exact ID, and a list of selections. Each selection has the requirement it serves, the phrase the user used, and the selected targets with their names, bounds and similar_group.
For each selection decide, target by target, whether that target is part of what the phrase names. A selection is often too wide in the same few ways: the hardware that holds a part is taken as the part (a wheel's bracket, brake or axle; a handle's screws; a hinge on a door), a neighbouring panel is swept in, or inner mechanisms are included for a word that names the visible shell. A word for the whole product or an assembly does cover all the parts that visibly form it.
Return one entry per selection, in the same order, with its index. wrong_ids lists only the selected targets that are NOT what the phrase names; leave it empty when the selection is right. confidence is your honest estimate from 0 to 1 that every target you listed is wrong. When unsure about a target, do not list it: a wrongly removed part is as bad as a wrongly included one. You cannot add targets. Return data only.'''

LOCATOR_PROMPT = '''You locate parts of a 3D product model. You answer one question only: which catalogue targets does this requirement refer to?
You receive the user's full instruction for context and ONE requirement taken from it, the current render (CURRENT_RENDER), labelled part-catalogue images (PART_CATALOG_n) showing every target with its exact ID, and target_evidence: per target its ID, object and material names, bounds, component count, nearest neighbours and, for repeated copies of one part, a similar_group.
Work from all of it together: names often say what a part is, the catalogue images show its shape, bounds and neighbours show where it sits, the render shows what the user is looking at. Names can be wrong or generic; shape and position decide.
Return one group per distinct thing the requirement names (for "the key and the keyhole" that is two groups). phrase is the user's own words for it, copied from the requirement. target_ids lists every target that belongs to it: a word for the whole product or an assembly (the body, the frame) covers all the parts that visibly form it, including repeated copies, both sides and inner faces that share its surface. Do not add neighbouring parts the user did not name, such as the brackets of a wheel or the screws on a panel.
confidence is your own honest estimate from 0 to 1 that the group is exactly what the user means.
expected_instances and count_quote: only if the requirement itself states how many parts are meant, give that number and the exact substring that states it; otherwise 0 and an empty string. Never count parts yourself.
If something the requirement names cannot be found, list it under unresolved with the closest reason and leave it out of groups. Do not guess to avoid an unresolved entry, and do not return a group merely because an earlier attempt chose it: previous_attempt, when present, is a rejected attempt and its measured failure.
You do not choose materials, colours or any change. Return data only.'''

FINISHER_PROMPT = '''You are a look-development artist for product photography. You answer one question only: which surface values make the located parts look the way this requirement describes?
You receive the user's full instruction for context, ONE requirement taken from it, the parts it refers to (already located; you cannot change them) with their measured current surface values where known, the current render, and the fields you may set.
Return one finish per phrase. values holds only fields from allowed_fields and only those the requirement calls for:
- preset replaces the whole material type and its base colour; color is an sRGB hex;
- roughness: 0 mirror-sharp reflections, 1 fully diffuse; metallic: 1 bare metal, 0 everything else (painted metal is 0);
- coat: a clear lacquer layer over the base, 0 none to 1 full. Sprayed paint, lacquer, glaze and moulded gloss have one;
- transmission: 1 for clear glass.
Choose physically plausible, clearly visible values: "reflective" sprayed metal is a low-roughness base under a clear coat, stainless steel is metallic with low to medium roughness, and so on. If measured current values already match, move far enough that the change is visible.
authorizations: a colour change needs capability color_change, a preset needs BOTH material_replacement and color_change, each with quote = an exact substring of the requirement that grants it. If the user's words do not grant it, do not use that field: keep the colour and express the finish through roughness, metallic and coat instead.
hard_protections and the requirement's protections are binding: under preserve_color return neither color nor preset.
summary is one short sentence in the user's language describing the parameter change, never a claimed visual result. Return data only.'''


def settings(environment=None):
    """Whether the specialist stage runs, and how many model calls it may spend."""
    values = dict(os.environ if environment is None else environment)
    if environment is None:
        try:
            import ai_config
            values.update(ai_config.read_env())
        except (ImportError, OSError):
            pass
    enabled = str(values.get(ENV_ENABLED, '1')).strip().lower() not in ('0', 'false', 'no', 'off')
    try:
        calls = int(str(values.get(ENV_MAX_CALLS, DEFAULT_MAX_CALLS)).strip())
    except ValueError:
        calls = DEFAULT_MAX_CALLS
    verify = str(values.get(ENV_VERIFY, '1')).strip().lower() not in ('0', 'false', 'no', 'off')
    return {'enabled': enabled, 'max_calls': max(0, min(12, calls)), 'verify': verify}


def _object(properties, required=None):
    return {'type': 'object', 'properties': properties,
            'required': list(properties if required is None else required), 'additionalProperties': False}


def _text(maximum=600, minimum=1):
    return {'type': 'string', 'minLength': minimum, 'maxLength': maximum}


def locator_schema(evidence):
    ids = [target['id'] for target in evidence.get('targets', [])]
    group = _object({'phrase': _text(160), 'target_ids': {'type': 'array', 'items': {'type': 'string', 'enum': ids},
                                                         'minItems': 1, 'maxItems': 160},
                     'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
                     'expected_instances': {'type': 'integer', 'minimum': 0, 'maximum': 10000},
                     'count_quote': _text(160, 0)})
    missing = _object({'phrase': _text(160), 'reason': {'type': 'string', 'enum': list(UNRESOLVED_REASONS)}})
    return _object({'groups': {'type': 'array', 'items': group, 'minItems': 0, 'maxItems': 8},
                    'unresolved': {'type': 'array', 'items': missing, 'minItems': 0, 'maxItems': 8}})


def verifier_schema(evidence, count):
    ids = [target['id'] for target in evidence.get('targets', [])]
    entry = _object({'index': {'type': 'integer', 'minimum': 0, 'maximum': max(0, count - 1)},
                     'wrong_ids': {'type': 'array', 'items': {'type': 'string', 'enum': ids}, 'minItems': 0, 'maxItems': 160},
                     'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1}})
    return _object({'selections': {'type': 'array', 'items': entry, 'minItems': count, 'maxItems': count}})


def value_fields(patch_schema):
    """Surface fields the current scene lets a local material edit set."""
    item = patch_schema.get('properties', {}).get('object_material_overrides', {}).get('items')
    if not item:
        return {}
    return {name: copy.deepcopy(schema) for name, schema in item['properties'].items()
            if name not in ('object_name', 'material_name')}


def finisher_schema(patch_schema, phrases):
    import semantic_planner
    fields = value_fields(patch_schema)
    finish = _object({'phrase': {'type': 'string', 'enum': list(phrases)}, 'values': _object(fields, [])})
    grant = _object({'capability': {'type': 'string', 'enum': list(semantic_planner.AUTHORIZATIONS)}, 'quote': _text(2000)})
    return _object({'finishes': {'type': 'array', 'items': finish, 'minItems': 1, 'maxItems': len(phrases)},
                    'authorizations': {'type': 'array', 'items': grant, 'minItems': 0,
                                       'maxItems': len(semantic_planner.AUTHORIZATIONS)},
                    'summary': _text()})


class _Unavailable(Exception):
    """The model could not be reached or answered outside its schema."""


def _ask(session, system, facts, schema, image_path=None, evidence_images=None):
    import ai_client as ai
    if session['calls'] >= session['max_calls']:
        raise _Unavailable('budget')
    session['calls'] += 1
    try:
        body = ai._payload(session['config'], system, facts, schema, image_path=image_path, label_images=True,
                           evidence_images=evidence_images, max_tokens=4096)
        output = ai._response(ai._post(session['config'], body), session['config'], ai.TOOL_NAME)
        ai._check_schema(output, schema)
    except ai.SchemaValidationError as exc:
        raise _Unavailable(exc.feedback.get('code', 'schema')) from exc
    except ValueError as exc:
        raise _Unavailable('transport') from exc
    return output


def _material_patch(patch):
    return isinstance(patch, dict) and any(patch.get(root) for root in MATERIAL_ROOTS)


def eligible(row):
    """A pending requirement a specialist may still make executable."""
    if row.get('status') != 'needs_clarification' or row.get('kind') == 'preservation' or row.get('_blocked'):
        return False
    failed = row.get('_failed_row')
    if failed is not None:
        return 'material' in failed.get('domains', []) or _material_patch(failed.get('patch'))
    # Left pending by the planner itself: only when it called it a material matter.
    return row.get('domains') == ['material']


def _previous_groups(failed, evidence):
    """Bindings of a rejected row whose IDs are real catalogue targets."""
    known = {target['id'] for target in evidence.get('targets', [])}
    groups = []
    for binding in (failed or {}).get('target_bindings', []) if isinstance((failed or {}).get('target_bindings'), list) else []:
        if (not isinstance(binding, dict) or binding.get('purpose') != 'material' or not isinstance(binding.get('phrase'), str)
                or not isinstance(binding.get('target_ids'), list) or not binding['target_ids']
                or any(ident not in known for ident in binding['target_ids'])
                or len(set(binding['target_ids'])) != len(binding['target_ids'])):
            return []
        groups.append({'phrase': binding['phrase'], 'target_ids': list(binding['target_ids']),
                       'expected_instances': binding.get('expected_instances', 0) if type(binding.get('expected_instances', 0)) is int else 0,
                       'count_quote': binding.get('count_quote', '') if isinstance(binding.get('count_quote', ''), str) else ''})
    if len({group['phrase'] for group in groups}) != len(groups):
        return []
    return groups


def _uniform_values(failed, groups, evidence, analysis):
    """Values a rejected patch gave the located targets, when unambiguous."""
    import semantic_planner
    if not failed or not _material_patch(failed.get('patch')):
        return None
    try:
        scope = semantic_planner._material_scope(failed['patch'], analysis)
    except (KeyError, TypeError, AttributeError):
        return None
    targets = {target['id']: (target['object_name'], target['material_name']) for target in evidence.get('targets', [])}
    result = {}
    for group in groups:
        seen = {json.dumps(scope[targets[ident]], sort_keys=True, ensure_ascii=False)
                for ident in group['target_ids'] if targets[ident] in scope}
        if len(seen) != 1:
            # No value for this group, or different values inside it: let the finisher decide.
            everything = {json.dumps(values, sort_keys=True, ensure_ascii=False) for values in scope.values()}
            if seen or len(everything) != 1:
                return None
            seen = everything
        values = json.loads(next(iter(seen)))
        if not values:
            return None
        result[group['phrase']] = values
    return result


def locate(session, text, row, failed, feedback, facts, images):
    """Locator agent: the catalogue targets one requirement names."""
    evidence = session['evidence']
    request = {'instruction': text,
               'requirement': {'quote': row['quote'], 'interpretation': row.get('interpretation', '')},
               'target_evidence': target_evidence.model_evidence(evidence),
               'evidence_notes': list(facts.get('evidence_notes', []))[:8]}
    previous = _previous_groups(failed, evidence)
    if previous or feedback:
        request['previous_attempt'] = {'groups': [{'phrase': group['phrase'], 'target_ids': group['target_ids']} for group in previous],
                                       'failure': {key: value for key, value in (feedback or {}).items()
                                                   if key in ('code', 'expected_instances', 'measured_instances', 'measured_groups',
                                                              'edited_without_binding', 'bound_without_edit')}}
    output = _ask(session, LOCATOR_PROMPT, request, locator_schema(evidence), image_path=session['image_path'],
                  evidence_images=images)
    groups = output['groups']
    if output['unresolved'] or not groups:
        reason = output['unresolved'][0]['reason'] if output['unresolved'] else 'not_in_catalogue'
        return None, {'result': 'unresolved', 'reason': reason}
    if len({group['phrase'] for group in groups}) != len(groups) or any(len(set(group['target_ids'])) != len(group['target_ids']) for group in groups):
        return None, {'result': 'rejected', 'reason': 'duplicate_selection'}
    confidence = min(group['confidence'] for group in groups)
    if confidence < MIN_CONFIDENCE:
        return None, {'result': 'unresolved', 'reason': 'ambiguous', 'confidence': round(confidence, 2)}
    located = [{'phrase': group['phrase'], 'target_ids': list(group['target_ids']),
                'expected_instances': group['expected_instances'], 'count_quote': group['count_quote']} for group in groups]
    return located, {'result': 'located', 'targets': sum(len(group['target_ids']) for group in groups),
                     'groups': len(groups), 'confidence': round(confidence, 2)}


def finish(session, text, row, groups, feedback, facts):
    """Finisher agent: surface values for targets that are already located."""
    evidence = session['evidence']
    targets = {target['id']: target for target in evidence.get('targets', [])}
    measured = {(item.get('object_name'), item.get('material_name')): item
                for item in facts.get('current_material_values', []) if isinstance(item, dict)}
    described = []
    for group in groups:
        parts = []
        for ident in group['target_ids'][:40]:
            target = targets[ident]; pair = (target['object_name'], target['material_name'])
            part = {'id': ident, 'object_name': pair[0], 'material_name': pair[1]}
            if pair in measured:
                part['current_values'] = copy.deepcopy(measured[pair].get('values', {}))
                if measured[pair].get('linked_fields'):
                    part['texture_driven_fields'] = list(measured[pair]['linked_fields'])
            parts.append(part)
        described.append({'phrase': group['phrase'], 'target_count': len(group['target_ids']), 'targets': parts})
    fields = value_fields(session['patch_schema'])
    if not fields:
        raise _Unavailable('no_material_fields')
    request = {'instruction': text,
               'requirement': {'quote': row['quote'], 'interpretation': row.get('interpretation', ''),
                               'protections': list(row.get('protections', []))},
               'located': described, 'allowed_fields': sorted(fields),
               'hard_protections': list(session['locks'])}
    if feedback and feedback.get('code'):
        request['previous_attempt'] = {'failure': {key: value for key, value in feedback.items()
                                                   if key in ('code', 'effect', 'plan_changed', 'expected_authorizations')}}
    phrases = [group['phrase'] for group in groups]
    output = _ask(session, FINISHER_PROMPT, request, finisher_schema(session['patch_schema'], phrases),
                  image_path=session['image_path'])
    by_phrase = {}
    for item in output['finishes']:
        if item['phrase'] in by_phrase or not item['values']:
            return None, None, None, {'result': 'rejected', 'reason': 'empty_or_duplicate_values'}
        by_phrase[item['phrase']] = item['values']
    if set(by_phrase) != set(phrases):
        return None, None, None, {'result': 'rejected', 'reason': 'incomplete_values'}
    return by_phrase, output['authorizations'], output['summary'], {'result': 'chosen', 'fields': sorted({field for values in by_phrase.values() for field in values})}


def verify(session, text, output, validated, revalidate, images):
    """Verifier agent: a second look at what the planner selected, before it is applied.

    One call covers every material selection of the requirements that are
    ready. Targets it is sure do not belong are taken out of the binding; the
    executor then drops their edits. A selection is only ever narrowed.
    """
    evidence = session['evidence']
    described = {target['id']: target for target in target_evidence.model_evidence(evidence)['targets']}
    selections = []
    for row in output['requirements']:
        checked = next((item for item in validated['rows'] if item['id'] == row['id']), None)
        if not checked or checked['status'] != 'ready':
            continue
        for index, binding in enumerate(row.get('target_bindings', [])):
            if binding.get('purpose') == 'material' and len(binding.get('target_ids', [])) > 1:
                selections.append((row['id'], index, binding))
    if not selections:
        return output, validated, None
    request = {'instruction': text, 'selections': [
        {'index': number, 'requirement': next(row['quote'] for row in output['requirements'] if row['id'] == ident),
         'phrase': binding['phrase'],
         'selected': [{key: described[target][key] for key in ('id', 'object_name', 'material_name', 'bounds', 'similar_group', 'similar_group_size')
                       if key in described[target]} for target in binding['target_ids'][:160]]}
        for number, (ident, _, binding) in enumerate(selections)]}
    answer = _ask(session, VERIFIER_PROMPT, request, verifier_schema(evidence, len(selections)),
                  image_path=session['image_path'], evidence_images=images)
    step = {'checked': len(selections), 'narrowed': 0, 'removed': 0}
    if sorted(item['index'] for item in answer['selections']) != list(range(len(selections))):
        return output, validated, {**step, 'result': 'rejected'}
    candidate = copy.deepcopy(output); changed = False
    for item in answer['selections']:
        ident, index, binding = selections[item['index']]
        wrong = [target for target in dict.fromkeys(item['wrong_ids']) if target in binding['target_ids']]
        if not wrong or item['confidence'] < VERIFY_CONFIDENCE:
            continue
        kept = [target for target in binding['target_ids'] if target not in wrong]
        if not kept:
            step['disputed'] = step.get('disputed', 0) + 1      # all of it wrong: not something to fix by narrowing
            continue
        row = next(entry for entry in candidate['requirements'] if entry['id'] == ident)
        row['target_bindings'][index]['target_ids'] = kept
        step['narrowed'] += 1; step['removed'] += len(wrong); changed = True
    if not changed:
        return output, validated, {**step, 'result': 'confirmed' if not step.get('disputed') else 'disputed'}
    try:
        checked = revalidate(candidate)
    except Exception as exc:
        return output, validated, {'checked': len(selections), 'narrowed': 0, 'removed': 0, 'result': 'rejected',
                                   'reason': getattr(exc, 'feedback', {}).get('code', 'validation')}
    before = {item['id'] for item in validated['rows'] if item['status'] in ('ready', 'already_satisfied')}
    after = {item['id'] for item in checked['rows'] if item['status'] in ('ready', 'already_satisfied')}
    if not before <= after:
        # Narrowing broke something that was valid (a stated count, say): keep the planner's selection.
        return output, validated, {'checked': len(selections), 'narrowed': 0, 'removed': 0, 'result': 'rejected', 'reason': 'validation'}
    return candidate, checked, {**step, 'result': 'narrowed'}


def build_row(original, groups, values, authorizations, summary, evidence):
    """Executor: an ordinary planner row from specialist answers."""
    targets = {target['id']: target for target in evidence.get('targets', [])}
    entries = {}
    for group in groups:
        for ident in group['target_ids']:
            target = targets[ident]
            entries.setdefault((target['object_name'], target['material_name']), {}).update(copy.deepcopy(values[group['phrase']]))
    patch = {key: copy.deepcopy(value) for key, value in (original.get('patch') or {}).items() if key not in MATERIAL_ROOTS} \
        if isinstance(original.get('patch'), dict) else {}
    patch['object_material_overrides'] = [{'object_name': pair[0], 'material_name': pair[1], **entry} for pair, entry in entries.items()]
    domains = list(dict.fromkeys([domain for domain in original.get('domains', []) if isinstance(domain, str)] + ['material']))
    return {'id': original['id'], 'quote': original['quote'], 'interpretation': original['interpretation'],
            'kind': original['kind'], 'domains': domains, 'depends_on': list(original.get('depends_on', [])),
            'status': 'ready', 'protections': list(original.get('protections', [])),
            'authorizations': copy.deepcopy(authorizations), 'patch': patch,
            'target_bindings': [{'phrase': group['phrase'], 'purpose': 'material', 'target_ids': list(group['target_ids']),
                                 'expected_instances': group.get('expected_instances', 0),
                                 'count_quote': group.get('count_quote', '')} for group in groups],
            'summary': summary}


def assist(output, validated, revalidate, *, text, config, analysis, evidence, patch_schema, facts, image_path, catalogue_images, limits):
    """Run the specialists over requirements the planner left pending.

    ``revalidate(output)`` is the planner's own validation of a complete
    decision. Returns (output, validated, trace). Every accepted change has
    passed it; anything else leaves the planner's result as it was.
    """
    trace = []
    if (not limits or not limits.get('enabled') or not limits.get('max_calls') or not evidence
            or evidence.get('method') != 'scene_part_catalog' or not catalogue_images):
        return output, validated, trace
    session = {'config': config, 'calls': 0, 'max_calls': limits['max_calls'], 'evidence': evidence, 'analysis': analysis,
               'patch_schema': patch_schema, 'image_path': image_path, 'locks': validated.get('locks', [])}
    if limits.get('verify', True):
        try:
            output, validated, step = verify(session, text, output, validated, revalidate, catalogue_images)
            if step:
                trace.append({'verifier': step})
        except _Unavailable as exc:
            trace.append({'verifier': {'result': 'unavailable', 'reason': str(exc)}})
    for ident in [row['id'] for row in validated['rows'] if eligible(row)][:MAX_ROWS]:
        # An earlier success replaces the decision; always work on the current one.
        pending = next(row for row in validated['rows'] if row['id'] == ident)
        if not eligible(pending):
            continue
        original = next(row for row in output['requirements'] if row['id'] == ident)
        failed = pending.get('_failed_row')
        code = pending.get('_execution_error')
        feedback = {'code': code, **pending.get('_execution_details', {})} if code else {}
        steps = {'requirement': ident}
        try:
            groups = None if code in RELOCATE else _previous_groups(failed, evidence)
            if not groups:
                groups, steps['locator'] = locate(session, text, original, failed, feedback, facts, catalogue_images)
                if not groups:
                    reason = PENDING_REASONS.get(steps['locator'].get('reason'))
                    if reason:
                        # Pointing at the part in the preview remains available as a fallback.
                        pending['summary'] = reason; pending['_locate'] = True
                    trace.append({**steps, 'result': 'pending'}); continue
            values = _uniform_values(failed, groups, evidence, analysis) if code in RELOCATE else None
            authorizations = copy.deepcopy((failed or {}).get('authorizations', [])) if values else None
            summary = (failed or {}).get('summary') if values else None
            if not values or not isinstance(summary, str) or not summary:
                values, authorizations, summary, steps['finisher'] = finish(session, text, original, groups, feedback, facts)
                if not values:
                    trace.append({**steps, 'result': 'pending'}); continue
            candidate = copy.deepcopy(output)
            index = next(i for i, row in enumerate(candidate['requirements']) if row['id'] == ident)
            candidate['requirements'][index] = build_row(failed or original, groups, values, authorizations, summary, evidence)
            checked = revalidate(candidate)
        except _Unavailable as exc:
            trace.append({**steps, 'result': 'unavailable', 'reason': str(exc)})
            if str(exc) in ('budget', 'transport'):
                break
            continue
        except Exception as exc:  # a specialist answer the planner's validation rejects outright
            trace.append({**steps, 'result': 'rejected', 'reason': getattr(exc, 'feedback', {}).get('code', 'validation')})
            continue
        row = next(item for item in checked['rows'] if item['id'] == ident)
        before = {item['id'] for item in validated['rows'] if item['status'] in ('ready', 'already_satisfied')}
        after = {item['id'] for item in checked['rows'] if item['status'] in ('ready', 'already_satisfied')}
        if row['status'] in ('ready', 'already_satisfied') and before <= after:
            output, validated = candidate, checked
            trace.append({**steps, 'result': 'resolved', **({'repairs': row['_repairs']} if row.get('_repairs') else {})})
        else:
            # Say why the specialists' own attempt was turned down, in the validator's fixed words.
            if row.get('_execution_error') and isinstance(row.get('summary'), str):
                pending['summary'] = row['summary']
                pending['_execution_error'] = row['_execution_error']
            trace.append({**steps, 'result': 'rejected', 'reason': row.get('_execution_error', 'validation')})
    if trace:
        trace.append({'calls': session['calls']})
    return output, validated, trace

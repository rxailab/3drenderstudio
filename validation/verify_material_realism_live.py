"""Read-only audit of the UI material/realism acceptance project.

No application imports, API calls, Blender execution, or project writes. The only
written artifact is a JSON report below validation/. Run after UI renders finish:
  python validation/verify_material_realism_live.py
  python validation/verify_material_realism_live.py --require-final-pick --require-final-ai-review
"""
import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
PROJECT_ID = 'd2ae89ec57a74a4b8ccefdab05482118'


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def pair(item):
    return item.get('object_name'), item.get('material_name')


def subset(value, keys):
    return {key: value[key] for key in keys if key in value}


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def local_file(url, project_root):
    route = unquote(urlsplit(url).path)
    require(route.startswith('/files/'), 'Artifact is not a local /files/ URL')
    path = (DATA / route[len('/files/'):]).resolve()
    require(path.is_relative_to(project_root), 'Artifact escaped this project directory')
    require(path.is_file(), f'Missing artifact: {path}')
    return path


def review_summary(review):
    # Explicit whitelist: never copy credentials, HTTP headers, logs or raw responses.
    result = subset(review, ('source', 'provider', 'model', 'mode', 'status', 'score',
        'question', 'checked_at', 'ai_unavailable', 'parameter_changes_applied', 'needs_user'))
    realism = review.get('realism', {})
    result['realism'] = subset(realism, ('summary', 'priority_actions'))
    result['realism']['causes'] = [subset(cause, ('aspect', 'evidence', 'confidence',
        'suggestion', 'requires_user')) for cause in realism.get('causes', [])]
    result['issues'] = [subset(issue, ('code', 'severity', 'message', 'confidence',
        'object_name', 'material_name')) for issue in review.get('issues', [])]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default=PROJECT_ID)
    parser.add_argument('--baseline-version', type=int, default=2)
    parser.add_argument('--direct-version', type=int, default=3)
    parser.add_argument('--language-version', type=int, default=4)
    parser.add_argument('--require-final-pick', action='store_true')
    parser.add_argument('--require-final-ai-review', action='store_true')
    parser.add_argument('--report', default='material-realism-live-results.json')
    args = parser.parse_args()
    report_path = (ROOT / 'validation' / args.report).resolve()
    require(report_path.is_relative_to((ROOT / 'validation').resolve()), 'Report must stay in validation/')
    project_root = (DATA / args.project).resolve()
    require(project_root.parent == DATA.resolve(), 'Invalid project id')

    # URI mode=ro is intentional. Importing studio_store would enable WAL/create tables.
    connection = sqlite3.connect((DATA / 'studio.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        row = connection.execute('SELECT payload FROM records WHERE kind=? AND id=?',
                                 ('project', args.project)).fetchone()
        require(row is not None, 'Acceptance project does not exist')
        project = json.loads(row[0])
        jobs = {job['id']: job for job in (json.loads(row[0]) for row in
            connection.execute("SELECT payload FROM records WHERE kind='job'"))
            if job.get('project_id') == args.project}
    finally:
        connection.close()

    baseline, direct, language = args.baseline_version, args.direct_version, args.language_version
    plans = {entry['version']: entry['plan'] for entry in project.get('history', [])}
    plans[project['version']] = project['plan']
    for version in (baseline, direct, language):
        require(version in plans, f'Version {version} not found; finish the UI workflow before auditing')
    direct_events = [event for event in project.get('material_edits', []) if event.get('version') == direct]
    language_events = [event for event in project.get('feedback', []) if event.get('version') == language]
    require(len(direct_events) == 1 and len(language_events) == 1, 'Expected one manual edit and one NL edit')
    manual, natural = direct_events[0], language_events[0]
    require(manual.get('kind') == 'edit' and manual.get('patch') == {'roughness': .65},
            'Manual patch must contain only roughness=0.65, without color')
    require(manual['source']['version'] == baseline and natural['source']['version'] == direct,
            'Edits must reference the preceding rendered versions')
    targets = {pair(target) for target in manual['targets']}
    require(len(targets) == 2, 'Manual acceptance edit must select two distinct material slots')
    selections = natural.get('hits', [])
    require(len(selections) == 1 and selections[0].get('hit'), 'NL acceptance must select one real surface')
    target = pair(selections[0])
    require(target in targets and target[0] == 'Label substrate', 'NL selection must be the original label')

    overrides = {version: {pair(item): item for item in plans[version].get('object_material_overrides', [])}
                 for version in (baseline, direct, language)}
    require(set(overrides[direct]) == targets == set(overrides[language]), 'Unexpected local material targets')
    for key in targets:
        require(overrides[direct][key].get('roughness') == .65, 'Both manually selected parts must be roughness 0.65')
        expected = .35 if key == target else .65
        require(overrides[language][key].get('roughness') == expected, 'NL changed the wrong material or value')
        for version in (direct, language):
            require('color' not in overrides[version][key], 'Untouched color must not enter a local material patch')
            require(overrides[version][key].get('preserve_original') is True, 'Original label/material must be preserved')
        if key != target:
            require(overrides[direct][key] == overrides[language][key], 'NL changed an unselected part')
    for before, after, allowed in ((baseline, direct, {'object_material_overrides'}),
            (direct, language, {'object_material_overrides', 'last_instruction', 'source'})):
        changes = {key for key in plans[before].keys() | plans[after].keys()
                   if plans[before].get(key) != plans[after].get(key)}
        require(changes <= allowed, f'Unexpected plan changes between v{before} and v{after}: {changes - allowed}')
    instruction = plans[language].get('last_instruction', {})
    require(instruction.get('source') == 'ai' and instruction.get('used_image') is True,
            'NL edit must have a recorded image-aware AI result')
    require(instruction.get('text') == natural.get('text'), 'NL audit text must match the event')
    require([change.get('path') for change in instruction.get('changes', [])] == ['object_material_overrides'],
            'NL actual change list must contain only local material overrides')

    selected_outputs = {}
    for version, job_id in ((baseline, manual['source']['output_id']),
            (direct, manual['job_id']), (language, natural['job_id'])):
        matches = [output for output in project.get('outputs', []) if output.get('version') == version
            and output.get('job_id') == job_id and output.get('shot_id') == manual['source']['shot_id']
            and output.get('quality') == 'preview']
        require(len(matches) == 1, f'Expected one actual preview for v{version} and job {job_id}')
        selected_outputs[version] = matches[0]
    require(natural['source']['output_id'] == selected_outputs[direct]['job_id'], 'NL references a different manual render')

    rendered, blend_versions = [], {}
    for version, output in selected_outputs.items():
        job = jobs.get(output['job_id'])
        require(job and job.get('status') == 'completed' and not job.get('error'), f'v{version} job did not complete')
        require(job.get('version') == version and job.get('quality') == 'preview'
                and output['shot_id'] in job.get('completed', []), 'Job/output identity mismatch')
        require(job['plan'] == plans[version], f'v{version} immutable job plan differs from project history')
        paths = {kind: local_file(output[kind + '_url'], project_root) for kind in ('png', 'jpg', 'blend', 'result')}
        blend_versions[paths['blend']] = version
        result = read_json(paths['result'])
        request = read_json(paths['result'].parent / 'request.json')
        require(result.get('success') is True, 'Blender result did not report success')
        require(request.get('plan') == job['plan'] and request.get('quality') == 'preview'
                and request.get('shot', {}).get('id') == output['shot_id'], 'Worker request/job mismatch')
        require((result.get('width'), result.get('height')) == (512, 512)
                and (output.get('width'), output.get('height')) == (512, 512), 'Unexpected reported preview dimensions')
        files = {}
        for kind, expected_format in (('png', 'PNG'), ('jpg', 'JPEG')):
            path = paths[kind]
            require(result['files'][kind] == path.name, 'Stored image differs from Blender result filename')
            with Image.open(path) as image:
                require(image.format == expected_format and image.size == (512, 512), 'Wrong actual image format/dimensions')
                image.verify()
            with Image.open(path) as image:
                image.load()
                require(image.size == (512, 512), 'Image failed full decoding')
            files[kind] = {'path': path.relative_to(ROOT).as_posix(), 'format': expected_format,
                          'width': 512, 'height': 512, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
        require(paths['blend'].stat().st_size > 100 and result['files']['blend'] == paths['blend'].name,
                'Expected saved Blender scene')
        operations = [operation for operation in result.get('operations', [])
                      if operation.get('operation') == 'object_material_override']
        require({pair(op['parameters']): op['parameters'] for op in operations} == overrides[version],
                'Actual Blender operations do not match local material plan')
        require(all(operation.get('applied_to') for operation in operations), 'A material override had no actual target')
        review = output.get('review', {})
        require(isinstance(review.get('realism', {}).get('summary'), str), 'Preview is missing a realism report')
        rendered.append({'version': version, 'job': subset(job, ('id', 'status', 'version', 'quality',
            'completed', 'started_at', 'finished_at')), 'images': files,
            'blend': {'path': paths['blend'].relative_to(ROOT).as_posix(), 'sha256': sha256(paths['blend'])},
            'blender_version': result.get('blender_version'), 'device': result.get('device'),
            'render_seconds': result.get('render_seconds'), 'applied_material_operations': operations,
            'review': review_summary(review)})

    require(selected_outputs[baseline].get('review', {}).get('source') == 'ai', 'Baseline must have an actual AI review')
    pick_evidence = []
    for result_path in sorted((project_root / 'feedback-picks').glob('*/result.json')):
        request_path = result_path.parent / 'request.json'
        if not request_path.is_file():
            continue
        request, result = read_json(request_path), read_json(result_path)
        version = blend_versions.get(Path(request.get('blend_path', '')).resolve())
        if version is None or result.get('success') is not True:
            continue
        for hit in result.get('hits', []):
            if not hit.get('hit') or pair(hit) not in targets:
                continue
            info = hit.get('material_info', {})
            require(info.get('source') == 'rendered_scene', 'Picker material value was not read from the rendered scene')
            if version in (direct, language):
                require(abs(info['roughness'] - overrides[version][pair(hit)]['roughness']) < .00001,
                        'Actual Blender shader roughness differs from the plan')
            pick_evidence.append({'version': version, 'result': result_path.relative_to(ROOT).as_posix(),
                **subset(hit, ('object_name', 'material_name', 'material_render_name', 'x', 'y')),
                'material_info': subset(info, ('source', 'value_basis', 'color', 'roughness', 'metallic',
                    'transmission', 'ior', 'linked', 'textures', 'editable'))})
    for key in targets:
        base_hits = [hit for hit in pick_evidence if hit['version'] == baseline and pair(hit) == key]
        for hit in [hit for hit in pick_evidence if hit['version'] > baseline and pair(hit) == key]:
            require(base_hits, 'Cannot verify original color without baseline scene pick')
            require(hit['material_info'].get('color') == base_hits[0]['material_info'].get('color'),
                    'Actual shader base color changed despite roughness-only edit')
    require(any(hit['version'] == direct and pair(hit) == target for hit in pick_evidence),
            'Need actual v3 label material readback through UI picking')
    final_pick = any(hit['version'] == language and pair(hit) == target for hit in pick_evidence)
    final_review = selected_outputs[language].get('review', {})
    final_ai = final_review.get('source') == 'ai' and not final_review.get('ai_unavailable')
    if args.require_final_pick:
        require(final_pick, 'Click the final label in the UI to record real v4 material readback, then rerun')
    if args.require_final_ai_review:
        require(final_ai, 'Final actual AI review has not succeeded; rerun after UI diagnosis completes')
    pending = []
    if not final_pick:
        pending.append('Final label shader value has not yet been read back by UI picker; executed material operation is verified.')
    if not final_ai:
        pending.append('Final preview review currently records rules fallback; this is not counted as visual AI success.')
    report = {'audited_at': datetime.now(timezone.utc).isoformat(), 'project_id': args.project,
        'project_version_at_snapshot': project['version'], 'status': 'passed_with_pending_checks' if pending else 'passed',
        'scope': 'Read-only audit of actual normal-UI renders; no project changes, new renders or API calls.',
        'checks': {'actual_png_jpg_512_square_and_complete': True, 'job_history_request_consistent': True,
            'manual_multiselect_roughness_only': True, 'nl_selected_label_only': True, 'untouched_color_not_sent': True,
            'unselected_body_preserved': True, 'actual_v3_label_shader_readback': True,
            'actual_final_label_shader_readback': final_pick, 'baseline_visual_ai_review': True,
            'final_visual_ai_review': final_ai, 'image_aware_nl_source_ai': True},
        'manual_edit': subset(manual, ('id', 'kind', 'source', 'targets', 'patch', 'version', 'job_id')),
        'natural_language_edit': {**subset(natural, ('id', 'source', 'text', 'scope', 'version', 'job_id')),
            'instruction': subset(instruction, ('text', 'source', 'provider', 'model', 'used_image', 'changes'))},
        'renders': rendered, 'actual_scene_picks': pick_evidence, 'pending': pending,
        'limits': ['This checks 512px previews; it does not certify 4K output or visual realism.',
                   'AI appearance assessments are recorded observations and suggestions, not ground-truth material measurements.']}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'MATERIAL_REALISM_LIVE_AUDITED {report["status"]}: {report_path.name}')


if __name__ == '__main__':
    main()

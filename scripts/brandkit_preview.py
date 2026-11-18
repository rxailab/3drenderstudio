"""Re-render one brand-kit template from the jobs a listing build kept (``<build>/jobs/<T>.json``).

    python scripts/brandkit_preview.py <build folder> T01 [--index 0] [--set key=json ...] [--lang en] [--out file.png]

Prints the QA issues and writes the PNG (default: <build>/preview/<T>-<index>.png). ``--set`` overrides a job field
with a JSON value (``--set mode='"band"'``), to try options a template offers before the set builder passes them.
Used while designing templates: a change to brandkit/templates or brandkit/engine shows up without running Blender
or the image model again.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brandkit_render  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('build'); parser.add_argument('template'); parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--set', action='append', default=[]); parser.add_argument('--lang'); parser.add_argument('--out')
    args = parser.parse_args()
    build = Path(args.build).resolve()
    jobs = json.loads((build / 'jobs' / (args.template + '.json')).read_text(encoding='utf-8'))
    job = jobs[min(args.index, len(jobs) - 1)]
    for item in args.set:
        key, _, value = item.partition('=')
        job[key] = json.loads(value)
    if args.lang:
        job['lang'] = args.lang
    out = Path(args.out) if args.out else build / 'preview' / ('%s-%d.png' % (args.template, args.index))
    qa = brandkit_render.render(args.template, job, out, build / 'preview')
    print(json.dumps({'png': str(out), 'versions': len(jobs), 'layout_issues': brandkit_render.layout_issues(qa),
                      'other_issues': [row for row in qa.get('issues', []) if row not in brandkit_render.layout_issues(qa)],
                      'notes': qa.get('notes', [])}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()

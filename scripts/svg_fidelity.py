"""How closely the editable SVG export matches the rendered page, on saved jobs.

    python scripts/svg_fidelity.py FIXTURE_DIR [FIXTURE_DIR ...] [--out DIR] [--templates T01,T12]

Each fixture folder holds ``jobs/<T>.json`` (a list of job versions; the last one is used) and an ``assets/`` folder.
Asset paths in the jobs are absolute; any that no longer exist are pointed at ``assets/<file name>`` in the fixture.
Every page is rendered to PNG, exported to SVG, the SVG drawn back in the headless browser at the same size, and the
two compared: the mean absolute difference (0-255, over RGB) and the share of pixels whose largest channel
difference is over 40. A side-by-side image (page | SVG | difference x4) is written for each page.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brandkit_render  # noqa: E402

MEAN_LIMIT = 6.0
OFF_LIMIT = 0.03


def fixture_job(folder, template_id, version=-1):
    """The saved job for a template, its asset paths repaired against the fixture's own assets/ folder."""
    folder = Path(folder)
    versions = json.loads((folder / 'jobs' / (template_id + '.json')).read_text(encoding='utf-8'))
    job = versions[version] if isinstance(versions, list) else versions
    job = json.loads(json.dumps(job))
    for record in (job.get('assets') or {}).values():
        path = Path(record.get('path') or '')
        if not path.is_file():
            path = folder / 'assets' / path.name
        record['path'] = str(path.resolve()); record['file'] = path.resolve().as_uri()
    return job


def compare(png_a, png_b):
    """(mean difference out of 255, share of pixels off by more than 40 in any channel, the difference picture)."""
    from PIL import ImageChops, ImageStat
    a, b = Image.open(png_a).convert('RGB'), Image.open(png_b).convert('RGB')
    diff = ImageChops.difference(a, b)
    mean = sum(ImageStat.Stat(diff).mean) / 3
    red, green, blue = diff.split()
    largest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = largest.histogram()
    return mean, sum(histogram[41:]) / max(1, sum(histogram)), largest


def side_by_side(png_a, png_b, diff, out):
    a, b = Image.open(png_a).convert('RGB'), Image.open(png_b).convert('RGB')
    d = diff.point(lambda value: min(255, value * 4)).convert('RGB')
    sheet = Image.new('RGB', (a.width * 3, a.height), 'white')
    for i, image in enumerate((a, b, d)):
        sheet.paste(image, (i * a.width, 0))
    sheet.thumbnail((2400, 800))
    sheet.save(out)


def check(template_id, job, out_dir, name):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    png = out_dir / (name + '.png'); svg = out_dir / (name + '.svg'); back = out_dir / (name + '.svg.png')
    qa = brandkit_render.render(template_id, job, png, work=out_dir)
    brandkit_render.export_svg(template_id, job, svg, work=out_dir)
    brandkit_render.rasterize_svg(svg, back, job['canvas']['width'], job['canvas']['height'])
    mean, off, diff = compare(png, back)
    side_by_side(png, back, diff, out_dir / (name + '.compare.jpg'))
    return {'page': name, 'template': template_id, 'mean': round(mean, 2), 'off40': round(off, 4), 'svg_bytes': svg.stat().st_size,
            'ok': mean < MEAN_LIMIT and off < OFF_LIMIT, 'qa': qa}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('fixtures', nargs='+')
    parser.add_argument('--out', default=str(Path(tempfile.gettempdir()) / 'svg_fidelity'))
    parser.add_argument('--templates', default='')
    args = parser.parse_args()
    if not brandkit_render.available():
        sys.exit('no Chrome/Chromium/Edge found')
    only = {t for t in args.templates.split(',') if t}
    rows = []
    for folder in map(Path, args.fixtures):
        for path in sorted((folder / 'jobs').glob('*.json')):
            template_id = path.stem
            if only and template_id not in only:
                continue
            job = fixture_job(folder, template_id)
            try:
                row = check(template_id, job, Path(args.out) / folder.name, template_id)
            except brandkit_render.RenderError as exc:
                row = {'page': template_id, 'template': template_id, 'error': str(exc), 'ok': False}
            row.pop('qa', None); row['fixture'] = folder.name
            rows.append(row)
            print('%-12s %-4s mean %6s  off>40 %7s  %s' % (folder.name, template_id, row.get('mean'), row.get('off40'), 'ok' if row['ok'] else 'FAIL'), flush=True)
    (Path(args.out) / 'fidelity.json').write_text(json.dumps(rows, indent=1))
    sys.exit(0 if all(row['ok'] for row in rows) else 1)


if __name__ == '__main__':
    main()

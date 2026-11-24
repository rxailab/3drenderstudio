"""Make the thumbnails the listing set picker shows on its buttons (static/listing-thumbs/).

    python scripts/make_listing_thumbs.py [--data-root D] --build <listing build folder> [--style-pages T12,T01] [--accent #rrggbb]
        --page T12=<png> --page T01=<png> ... --page T05=box --box "Name=<cut-out png>" ... --work <scratch folder> [--out static/listing-thumbs]

* style-<preset>.jpg: each preset of brand_kit.PRESETS in its own composition, two pages side by side on one image: the
  poster (T12) and the callout page (T01). The saved versions of those pages in ``--build`` (``<build>/jobs/<kind>.json``,
  the list lay_out tried, see scripts/brandkit_preview.py) are put through the preset's layout recipe
  (listing_jobs.preset_jobs) and restyled (type, ground, palette, font pairing), and the first version that passes the
  layout checks is drawn, as a build in that preset would. The presets differ in where the product and the words sit,
  so the pictures must show the pages laid out, not one page recoloured. Use a build made in the default preset
  ('frame'): its saved versions are the builder's own, with no recipe applied. A build with a scene picture shows the
  presets whose poster puts words over a scene (workshop, promo); without one those fall back as a build would.
  ``--accent`` sets a brand colour unlike the product (a red accent on a red product hid the accent type on the
  brand-colour ground).
* page-<kind>.jpg: a built page of each kind (``<project>/listing/build-*/NN-<kind>.png``), downscaled.
  A kind given as ``--page T05=box`` is laid out here instead: an "in the box" page of the ``--box`` cut-outs
  (pictures on white, one per item), since no product of the sample data has separately processed parts.

Paths that are not absolute (``--build``, ``--page``, ``--box``) are read under the data folder: ``--data-root``, else
STUDIO_DATA, else <repo>/data (studio_store.data_root). Everything is drawn from files on disk: the pictures a job names
are copied into ``--work`` first (the data folder is only read) and the font and icon downloads brandkit_assets would try
are refused, so a missing face shows as a fallback face rather than a network call. Set BRANDKIT_VENDOR to a folder
holding the downloaded faces and icons.

The committed files are made with (the projects are the sample products; run from the repo with the data folder set):

    --build a1003cd9af89419092ca5086c3604923/listing/build-00c126f23a --accent '#1f5fd1'
    --page T12=247a1294215f4d769d6382b834610da7/listing/build-ce2ce6ad72/01-T12.png    (digital caliper)
    --page T01=fc7c4e745088418db325068077e569ea/listing/build-584dbeb20b/02-T01.png    (canvas tote, Chinese)
    --page T03=a1003cd9af89419092ca5086c3604923/listing/build-00c126f23a/03-T03.png    (angle grinder)
    --page T13=7f63af299376468c95f4da2756e84a59/listing/build-912b50ae94/02-T13.png    (tool cabinet)
    --page T02=8df247ef2ecf4ff488521f2667e8c805/listing/build-f9e744e0e0/04-T02.png    (combination pliers)
    --page T04=9d605d0301aa4bd791b9c9d5ec642fd2/listing/build-f56901384f/03-T04.png    (service cart)
    --page T08=d12d7770a7e14e739e663f88c0bfa593/listing/build-1bbceb53f5/04-T08.png    (wall cabinet)
    --page T06=86d304c3c6ac4c7c9fc6dcaacc638965/listing/build-b9d0733f84/04-T06.png    (diagonal cutters)
    --page T05=box --box "Digital Caliper=247a…/build-ce2ce6ad72/assets/hero.png" --box "Combination Pliers=8df2…/assets/hero.png"
                   --box "Diagonal Cutters=86d3…/assets/hero.png" --box "Angle Grinder=a100…/assets/hero.png"
"""
import argparse
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brand_kit  # noqa: E402
import brandkit_assets  # noqa: E402
import brandkit_render  # noqa: E402
import listing_jobs  # noqa: E402
from listing_theme import _palette  # noqa: E402

SIZE = 480
QUALITY = 82
STYLE_PAGES = ('T12', 'T01')         # the poster and the callout page: where the presets' compositions differ most
GAP = 8                              # between the two pages of a style thumbnail
STAGE = (233, 234, 236)              # the neutral grey round them
MAX_TRIES = 8                        # versions tried per page before the one with the fewest problems is taken
SOFT_CHECKS = ('frame_cuts_part',)   # as listing_layout: a close-up crop clipping a small part is not a failed layout


def _offline(*_args, **_kwargs):
    raise OSError('make_listing_thumbs works offline')


def data_root(given=None):
    """The data folder the relative paths are read under: --data-root, else STUDIO_DATA, else <repo>/data."""
    chosen = given or os.environ.get('STUDIO_DATA', '').strip()
    return Path(chosen).expanduser().resolve() if chosen else ROOT / 'data'


def under(root, path):
    path = Path(path).expanduser()
    return path if path.is_absolute() else root / path


def _square(source):
    with Image.open(source) as image:
        image = image.convert('RGB')
        side = min(image.size)          # every page kind is built square today; a 4:5 page would lose its foot
        return image.crop((0, 0, side, side))


def save_thumb(source, target):
    """A square page downscaled to the contract size, as a small JPEG."""
    image = _square(source).resize((SIZE, SIZE), Image.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, 'JPEG', quality=QUALITY, optimize=True, progressive=True)
    return target


def side_by_side(sources, target):
    """Pages side by side on one SIZE x SIZE thumbnail, each a square as wide as its share, centred on a neutral grey."""
    side = (SIZE - GAP * (len(sources) + 1)) // len(sources)
    sheet = Image.new('RGB', (SIZE, SIZE), STAGE)
    for number, source in enumerate(sources):
        page = _square(source).resize((side, side), Image.LANCZOS)
        sheet.paste(page, (GAP + number * (side + GAP), (SIZE - side) // 2))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, 'JPEG', quality=QUALITY, optimize=True, progressive=True)
    return target


def _local_assets(job, folder):
    """The job with every picture it names copied into ``folder`` (the renderer reads them from there, never from data/)."""
    folder.mkdir(parents=True, exist_ok=True)
    assets = {}
    for key, value in (job.get('assets') or {}).items():
        copy = folder / Path(value['path']).name
        if not copy.exists():
            shutil.copy2(value['path'], copy)
        assets[key] = {**value, 'file': copy.resolve().as_uri(), 'path': str(copy.resolve())}
    return {**job, 'assets': assets}


def _brand(preset, job, accent=None):
    """``accent`` is a brand colour the seller set; without one the job's accent stands as the product's colour."""
    return (brand_kit.Brand({'preset': preset, 'accent': accent}) if accent else
            brand_kit.Brand({'preset': preset}, product_color=(job.get('palette') or {}).get('accent')))


def styled(job, preset, accent=None):
    """The same page job in another preset's look: its type, frame and ground with the palette they imply, and the font
    pairing the preset reads in. Its composition is preset_versions' business."""
    kind = job['template_id']; lang = job.get('lang', 'en')
    brand = _brand(preset, job, accent)
    return {**job, 'style': brand_kit.page_style(brand), 'palette': _palette(kind, brand),
            'fonts': brandkit_assets.job_fonts(brand_kit.fonts_of(brand, lang), lang)}


def preset_versions(kind, jobs, preset, accent=None):
    """A page's saved versions as a build in ``preset`` would try them: its layout recipe first, then the versions as
    they were, each in the preset's look."""
    return [styled(job, preset, accent) for job in listing_jobs.preset_jobs(kind, jobs, brand_kit.PRESETS[preset].get('layout'))]


def first_clean(kind, versions, png, work, tries=MAX_TRIES):
    """Render the versions in order and keep the first without hard layout problems (else the one with the fewest).
    Returns (the job drawn, its problems)."""
    best = None
    for number, job in enumerate(versions[:tries]):
        target = png.with_name('%s.try%d.png' % (png.stem, number))
        qa = brandkit_render.render(kind, job, target, work)
        hard = [row for row in brandkit_render.layout_issues(qa) if row.get('check') not in SOFT_CHECKS]
        if best is None or len(hard) < len(best[2]):
            best = (target, job, hard)
        if not hard:
            break
    shutil.copy2(best[0], png)
    return best[1], best[2]


def style_thumbs(build, out, work, accent=None, pages=STYLE_PAGES):
    """style-<preset>.jpg for every preset; returns (the files, the callout page's first saved job, for the box page)."""
    saved = {kind: [_local_assets(job, work / 'assets') for job in json.loads((build / 'jobs' / (kind + '.json')).read_text(encoding='utf-8'))]
             for kind in pages}
    made = []
    for preset in brand_kit.PRESETS:
        drawn = []
        for kind in pages:
            png = work / ('style-%s-%s.png' % (preset, kind))
            job, problems = first_clean(kind, preset_versions(kind, saved[kind], preset, accent), png, work)
            mode = {key: job[key] for key in ('mode', 'callout_style', 'label_side', 'split_side', 'center_type', 'frame') if key in job}
            print('style %s %s: %s%s' % (preset, kind, json.dumps(mode, ensure_ascii=False),
                                         ('  problems: ' + json.dumps(problems, ensure_ascii=False)[:300]) if problems else ''))
            drawn.append(png)
        made.append(side_by_side(drawn, out / ('style-%s.jpg' % preset)))
    return made, saved[pages[-1]][0]


def _cutout(path, folder, key):
    """A cut-out on white as a job asset, with the box its product fills (the template centres items by it)."""
    copy = folder / ('box-%s%s' % (key, Path(path).suffix))       # by item: every build names its cut-out hero.png
    shutil.copy2(path, copy)
    with Image.open(copy) as image:
        grey = image.convert('L').point(lambda value: 255 if value < 245 else 0)
        box = grey.getbbox() or (0, 0, image.width, image.height)
        width, height = image.size
    bbox = [round(box[0] / width, 4), round(box[1] / height, 4), round(box[2] / width, 4), round(box[3] / height, 4)]
    return brandkit_render.asset(copy, content_bbox=bbox)


def box_page(base, items, work, accent=None, lang='en'):
    """An "in the box" page (T05) of the given cut-outs, in the base job's fonts and accent and the default preset."""
    brand = _brand(brand_kit.DEFAULT_PRESET, base, accent)
    tokens = brandkit_render.template_info('T05')['tokens']
    folder = work / 'assets'; folder.mkdir(parents=True, exist_ok=True)
    assets, rows = {}, []
    for number, (name, path, qty) in enumerate(items):
        key = 'item%d' % number
        assets[key] = _cutout(path, folder, key)
        rows.append({'asset': key, 'name': name, **({'qty': qty} if qty else {})})
    job = {'template_id': 'T05', 'variant': 'studio', 'canvas': {'width': 1500, 'height': 1500}, 'lang': lang,
           'safe_margin': tokens.get('safe_margin', 72), 'min_margin': tokens.get('min_margin', 24),
           'palette': _palette('T05', brand), 'publishability': 'user_product', 'fonts': base.get('fonts') or brandkit_assets.job_fonts(None, lang),
           'style': brand_kit.page_style(brand), 'headline': "What's in the box" if lang == 'en' else '包装清单',
           'note': 'Items not shown to scale' if lang == 'en' else '图示物品未按实际比例', 'items': rows, 'assets': assets}
    png = work / 'page-T05.png'
    qa = brandkit_render.render('T05', job, png, work)
    issues = brandkit_render.layout_issues(qa)
    if issues:
        print('page T05: %s' % json.dumps(issues, ensure_ascii=False)[:400])
    return png


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--data-root', help='the data folder relative paths are read under (default: STUDIO_DATA, else <repo>/data)')
    parser.add_argument('--build', required=True, help='a listing build folder with saved jobs (<project>/listing/build-*), made in the default preset')
    parser.add_argument('--style-pages', default=','.join(STYLE_PAGES), help='the two page kinds a style thumbnail shows side by side')
    parser.add_argument('--accent', help='a brand colour (#rrggbb) for the style pages; pick one unlike the product, or the accent hides on it')
    parser.add_argument('--page', action='append', default=[], help='KIND=<built page png>, or T05=box')
    parser.add_argument('--box', action='append', default=[], help='"Name=<cut-out png>[=qty]" for the T05 page')
    parser.add_argument('--out', default=str(ROOT / 'static' / 'listing-thumbs'))
    parser.add_argument('--work', required=True, help='a scratch folder (rendered pages and copied pictures)')
    args = parser.parse_args()
    urllib.request.urlopen = _offline           # set here, not on import: the tests import this module
    root = data_root(args.data_root)
    build, out, work = under(root, args.build).resolve(), Path(args.out).resolve(), Path(args.work).resolve()
    pages = tuple(kind.strip() for kind in args.style_pages.split(',') if kind.strip())
    made, job = style_thumbs(build, out, work, args.accent, pages)
    for item in args.page:
        kind, _, source = item.partition('=')
        if source == 'box':
            items = []
            for row in args.box:
                name, path, *qty = row.split('=')
                items.append((name, str(under(root, path)), int(qty[0]) if qty else None))
            source = box_page(job, items, work, args.accent)
        made.append(save_thumb(under(root, source), out / ('page-%s.jpg' % kind)))
    for path in made:
        print('%s  %.1f KB' % (path, path.stat().st_size / 1024))


if __name__ == '__main__':
    main()
